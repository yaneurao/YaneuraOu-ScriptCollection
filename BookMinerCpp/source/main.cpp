#include "book_store.h"
#include "config.h"
#include "sfen_position.h"
#include "usi_engine.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <cctype>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <ctime>
#include <deque>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <random>
#include <regex>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <csignal>
#include <fcntl.h>
#include <pthread.h>
#include <sys/select.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace fs = std::filesystem;

namespace {

constexpr const char* BookDir = "book";
constexpr const char* BookBackupDir = "book/backup";
constexpr const char* BookDbName = "book_miner";
constexpr const char* PetaBookDbName = "peta_book";
constexpr const char* ThinkSfensName = "think_sfens.txt";
constexpr const char* ThinkUnsolvedSfensName = "think_unsolved_sfens.txt";
constexpr const char* EngineSettingsPath = "settings/engine_settings.json5";
constexpr const char* BookMinerSettingsPath = "settings/book_miner_settings.json5";
constexpr const char* BookMinerCppSettingsPath = "settings/book_miner_cpp_settings.json5";
constexpr int ThinkCommandPly = 6;
constexpr int TaskDeferSleepMilliseconds = 10;
constexpr int DefaultEvalLimit = 400;
constexpr int PlyMin = std::numeric_limits<int>::min();
constexpr int DefaultEvalRefutationMargin = 100;
constexpr int PetaDefaultInfEvalDiff = 99999;
constexpr int PetaDefaultMaxStep = 9999;
constexpr int DefaultStep2EvalDiff = 30;
constexpr int DefaultStep2MaxStep = 99999;
constexpr int PetaRejoinEvalDropDisabled = -9999;
constexpr std::size_t PetaRefutationProgressInterval = 100000;
constexpr std::size_t PetaUnsolvedProgressInterval = 100000;
constexpr std::size_t PetaOpponentProgressInterval = 100000;
constexpr const char* BookOpponentDir = "book/book_opponent";

struct CommandDefaults {
    int eval_diff = DefaultStep2EvalDiff;
    int max_step = DefaultStep2MaxStep;
    int game_ply_limit = 200;
    int book_extend_ply = ThinkCommandPly;
    int eval_limit = DefaultEvalLimit;
};

#ifdef _WIN32
void configure_windows_console()
{
    SetConsoleCP(CP_UTF8);
    SetConsoleOutputCP(CP_UTF8);
}
#else
void configure_windows_console()
{
}
#endif

bool is_yaneuraou_progress_bar_line(const std::string& line)
{
    constexpr std::string_view Prefix = "0% [";
    constexpr std::string_view Suffix = "] 100%";
    std::string_view view(line);
    while (!view.empty() && view.back() == '\r')
        view.remove_suffix(1);
    if (view.size() <= Prefix.size() + Suffix.size())
        return false;
    if (view.substr(0, Prefix.size()) != Prefix)
        return false;
    if (view.substr(view.size() - Suffix.size()) != Suffix)
        return false;

    view.remove_prefix(Prefix.size());
    view.remove_suffix(Suffix.size());
    return !view.empty() && std::all_of(view.begin(), view.end(), [](char ch) {
        return ch == '.';
    });
}

class Logger {
public:
    void open()
    {
        fs::create_directories("log");
        path_ = fs::path("log") / ("log_" + timestamp_compact() + ".log");
        file_.open(path_);
        print("log file : " + path_.string());
    }

    void print(const std::string& message)
    {
        std::scoped_lock lock(mutex_);
        const std::string line = "[" + timestamp_display() + "] " + message;
        std::cout << line << std::endl;
        if (file_)
            file_ << line << '\n';
    }

    void prompt()
    {
        std::cout << "[Q]uit [T]hink [H]elp> ";
        std::cout.flush();
    }

private:
    static std::tm local_time_now()
    {
        const auto now = std::chrono::system_clock::now();
        const std::time_t t = std::chrono::system_clock::to_time_t(now);
        std::tm tm{};
#ifdef _WIN32
        localtime_s(&tm, &t);
#else
        localtime_r(&t, &tm);
#endif
        return tm;
    }

    static std::string timestamp_compact()
    {
        const std::tm tm = local_time_now();
        char buffer[32]{};
        std::strftime(buffer, sizeof(buffer), "%Y%m%d%H%M%S", &tm);
        return buffer;
    }

    static std::string timestamp_display()
    {
        const std::tm tm = local_time_now();
        char buffer[32]{};
        std::strftime(buffer, sizeof(buffer), "%Y/%m/%d %H:%M:%S", &tm);
        return buffer;
    }

    fs::path path_;
    std::ofstream file_;
    std::mutex mutex_;
};

Logger* g_logger = nullptr;
std::mutex g_save_mutex;

void log_line(const std::string& message)
{
    if (g_logger)
        g_logger->print(message);
    else
        std::cout << message << std::endl;
}

class MiningStats {
public:
    void report(std::size_t position_count, std::uint64_t searched_nodes = 0)
    {
        std::uint64_t searched_positions = 0;
        std::uint64_t total_nodes = 0;
        {
            std::scoped_lock lock(mutex_);
            if (searched_nodes > 0)
            {
                ++searched_positions_;
                total_nodes_ += searched_nodes;
            }
            searched_positions = searched_positions_;
            total_nodes = total_nodes_;
        }

        std::ostringstream oss;
        oss << "[MiningProgress] positions=" << position_count;
        if (searched_positions > 0)
        {
            const auto avg_nodes = (total_nodes + searched_positions / 2) / searched_positions;
            oss << " searched_positions=" << searched_positions
                << " nodes=" << total_nodes
                << " avg_nodes=" << avg_nodes;
        }
        log_line(oss.str());
    }

private:
    std::mutex mutex_;
    std::uint64_t searched_positions_ = 0;
    std::uint64_t total_nodes_ = 0;
};

MiningStats g_mining_stats;
std::atomic<double> g_consecutive_node_ratio{0.8};

std::vector<std::string> split_ws(const std::string& line)
{
    std::istringstream iss(line);
    std::vector<std::string> out;
    std::string token;
    while (iss >> token)
        out.push_back(token);
    return out;
}

bool is_none_argument(const std::string& token)
{
    std::string lower = token;
    std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return lower == "none";
}

int parse_integer(const std::string& text)
{
    std::size_t parsed = 0;
    int value = 0;
    try { value = std::stoi(text, &parsed); }
    catch (const std::exception&) { throw std::invalid_argument("invalid integer: " + text); }
    if (parsed != text.size())
        throw std::invalid_argument("invalid integer: " + text);
    return value;
}

int parse_int_argument(const std::vector<std::string>& tokens, std::size_t index, int default_value)
{
    if (tokens.size() <= index || is_none_argument(tokens[index]))
        return default_value;
    return parse_integer(tokens[index]);
}

std::optional<int> parse_optional_int_argument(const std::vector<std::string>& tokens, std::size_t index)
{
    if (tokens.size() <= index || is_none_argument(tokens[index]))
        return std::nullopt;
    return parse_integer(tokens[index]);
}

bool has_considered(const std::optional<bookminer::PositionInfo>& position)
{
    return position.has_value() && !position->moves.empty();
}

struct BookPositionLookup {
    std::optional<bookminer::PositionInfo> position;
    bool flipped = false;
};

BookPositionLookup find_book_position_with_flip(const bookminer::BookStore& book, const std::string& sfen)
{
    const auto key = bookminer::PackedSfen::from_sfen(sfen);
    if (auto position = book.find_position_copy(key))
        return {std::move(position), false};

    if (auto position = book.find_position_copy(key.flipped()))
        return {std::move(position), true};

    return {};
}

BookPositionLookup find_considered_book_position_with_flip(const bookminer::BookStore& book, const std::string& sfen)
{
    const auto key = bookminer::PackedSfen::from_sfen(sfen);
    auto direct = book.find_position_copy(key);
    if (has_considered(direct))
        return {std::move(direct), false};
    auto flipped = book.find_position_copy(key.flipped());
    if (has_considered(flipped))
        return {std::move(flipped), true};
    if (direct)
        return {std::move(direct), false};
    if (flipped)
        return {std::move(flipped), true};
    return {};
}

std::vector<bookminer::MoveInfo> to_book_moves(const std::vector<bookminer::EngineMoveInfo>& engine_moves)
{
    std::vector<bookminer::MoveInfo> moves;
    moves.reserve(engine_moves.size());
    for (const auto& move : engine_moves)
    {
        const auto move16 = bookminer::move16_from_usi(move.move);
        if (move16 == 0)
            continue;
        moves.push_back(bookminer::MoveInfo{move16, static_cast<std::int16_t>(move.eval), 0});
    }
    return moves;
}

std::optional<std::pair<int, std::uint16_t>> get_best(const bookminer::PositionInfo& position)
{
    if (position.moves.empty())
        return std::nullopt;

    const bookminer::MoveInfo* best = nullptr;
    for (const auto& move : position.moves)
    {
        if (best == nullptr || move.eval > best->eval)
            best = &move;
    }
    if (best == nullptr)
        return std::nullopt;
    return std::make_pair(static_cast<int>(best->eval), best->move16);
}

std::uint16_t move16_in_sfen_orientation(const bookminer::MoveInfo& move, bool flipped)
{
    return flipped ? bookminer::flipped_move16(move.move16) : move.move16;
}

const bookminer::MoveInfo* find_moveinfo_by_oriented_move(
    const bookminer::PositionInfo& position,
    std::uint16_t oriented_move16,
    bool flipped)
{
    for (const auto& move : position.moves)
        if (move16_in_sfen_orientation(move, flipped) == oriented_move16)
            return &move;
    return nullptr;
}

struct BestMoveInfo {
    const bookminer::MoveInfo* info = nullptr;
    std::uint16_t oriented_move16 = 0;
};

BestMoveInfo best_moveinfo_in_sfen_orientation(const bookminer::PositionInfo& position, bool flipped)
{
    const bookminer::MoveInfo* best = nullptr;
    for (const auto& move : position.moves)
        if (best == nullptr || move.eval > best->eval)
            best = &move;

    if (best == nullptr)
        return {};
    return {best, move16_in_sfen_orientation(*best, flipped)};
}

bool is_refuted_move_by_peta_impact(
    const bookminer::BookStore& source_book,
    const bookminer::PositionInfo& peta_position,
    bool peta_flipped,
    const std::string& sfen,
    std::uint16_t oriented_move16,
    int peta_move_eval,
    int eval_refutation_margin)
{
    const auto old_hit = find_book_position_with_flip(source_book, sfen);
    if (!old_hit.position.has_value() || old_hit.position->moves.empty())
        return false;

    const auto old_best = best_moveinfo_in_sfen_orientation(*old_hit.position, old_hit.flipped);
    const auto* old_candidate = find_moveinfo_by_oriented_move(
        *old_hit.position,
        oriented_move16,
        old_hit.flipped);

    if (old_best.info == nullptr || old_candidate == nullptr)
        return false;
    if (old_best.oriented_move16 == oriented_move16)
        return false;

    const auto* peta_old_best = find_moveinfo_by_oriented_move(
        peta_position,
        old_best.oriented_move16,
        peta_flipped);
    if (peta_old_best == nullptr)
        return false;

    return peta_move_eval - static_cast<int>(peta_old_best->eval) >= eval_refutation_margin;
}

enum class TaskResult {
    Done,
    Deferred,
};

enum class ThinkOnceStatus {
    Done,
    Deferred,
    Position,
};

struct ThinkOnceResult {
    ThinkOnceStatus status = ThinkOnceStatus::Done;
    bookminer::PositionInfo position;
    std::string sfen;
    bool searched = false;
};

ThinkOnceResult think_sfen_once(
    bookminer::BookStore& book,
    bookminer::UsiEngine& engine,
    const std::string& sfen,
    int ply,
    int& last_thinking_ply,
    int max_book_ply,
    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash>& visited)
{
    if (ply >= max_book_ply)
    {
        log_line("max_book_ply reached. ply = " + std::to_string(ply)
            + ", max_book_ply = " + std::to_string(max_book_ply)
            + ", sfen = " + sfen);
        return {};
    }

    const auto sfen_key = bookminer::PackedSfen::from_sfen(sfen);
    const auto sfen_f_key = sfen_key.flipped();
    if (visited.find(sfen_key) != visited.end() || visited.find(sfen_f_key) != visited.end())
    {
        const auto current_ply = static_cast<std::uint16_t>(std::clamp(ply, 0, 65535));
        if (!book.update_position_ply(sfen_key, current_ply))
            (void)book.update_position_ply(sfen_f_key, current_ply);
        return {};
    }

    const std::string sfen_f = bookminer::flipped_sfen(sfen);
    bookminer::SearchLease lease = book.try_begin_search(sfen, sfen_f);
    if (!lease.acquired)
        return ThinkOnceResult{ThinkOnceStatus::Deferred, {}, sfen};

    visited.insert(sfen_key);
    visited.insert(sfen_f_key);

    struct LeaseGuard {
        bookminer::BookStore& book;
        bookminer::SearchLease& lease;
        ~LeaseGuard() { book.end_search(lease); }
    } guard{book, lease};

    if (has_considered(lease.position))
    {
        auto position = *lease.position;
        const auto current_ply = static_cast<std::uint16_t>(std::clamp(ply, 0, 65535));
        if (current_ply < position.ply)
        {
            book.update_position_ply(lease.sfen, current_ply);
            position.ply = current_ply;
        }
        return ThinkOnceResult{ThinkOnceStatus::Position, std::move(position), lease.sfen, false};
    }

    const double node_ratio = last_thinking_ply + 1 == ply ? g_consecutive_node_ratio.load() : 1.0;
    {
        std::ostringstream oss;
        oss << "[" << engine.thread_id() << "] " << lease.sfen << " " << ply << " , " << node_ratio;
        log_line(oss.str());
    }

    const std::string engine_position = "sfen " + lease.sfen + " " + std::to_string(ply);
    const auto engine_result = engine.go(engine_position, node_ratio, [](const std::string& message) {
        log_line(message);
    });
    last_thinking_ply = ply;

    book.merge_position(lease.sfen, static_cast<std::uint16_t>(std::clamp(ply, 0, 65535)), to_book_moves(engine_result.moves));
    g_mining_stats.report(book.size(), engine_result.searched_nodes);

    auto position = book.find_position_copy(lease.sfen);
    if (has_considered(position))
        return ThinkOnceResult{ThinkOnceStatus::Position, *position, lease.sfen, true};
    return {};
}

struct BranchTaskSpec {
    std::string position_command;
    int eval_limit = 0;
    int max_book_ply = 0;
    int think_command_ply = ThinkCommandPly;
    int job_id = 0;
    int branch_width = 0;
    int branch_depth = 0;
};

using EnqueueBranchTask = std::function<void(BranchTaskSpec)>;

struct TaskResume {
    bool best_line = false;
    std::string position_command;
    int rest_ply = 0;
    int rest_branch_depth = 0;
    int last_thinking_ply = PlyMin;
    int engine_id = -1;
    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> visited;
    bool branch_extension = false;
};

void forget_failed_position(std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash>& visited,
                            const std::string& sfen)
{
    const auto key = bookminer::PackedSfen::from_sfen(sfen);
    visited.erase(key);
    visited.erase(key.flipped());
}

bool peta_cut_by_eval_limit(const bookminer::PositionInfo&, bool, const std::string&,
                            std::optional<int>, const bookminer::BookStore*, bool);

std::vector<std::string> thinking_candidate_moves(
    const bookminer::PositionInfo& position,
    int eval_limit,
    std::optional<std::size_t> move_limit,
    const bookminer::BookStore& book,
    const std::string& sfen_with_ply)
{
    const auto best = get_best(position);
    if (!best.has_value())
        return {};
    if (peta_cut_by_eval_limit(position, false, sfen_with_ply, eval_limit, &book, true))
        return {};

    const std::size_t limit = move_limit.has_value()
        ? std::min<std::size_t>(*move_limit, position.moves.size())
        : position.moves.size();
    std::vector<std::string> candidate_moves;
    for (std::size_t i = 0; i < limit; ++i)
    {
        const auto& move_info = position.moves[i];
        if (std::abs(static_cast<int>(move_info.eval)) > eval_limit && move_info.move16 != best->second)
            continue;
        candidate_moves.push_back(bookminer::move16_to_usi(move_info.move16));
    }
    return candidate_moves;
}

void enqueue_branch_tasks(
    const std::string& current_sfen,
    int current_ply,
    const std::vector<std::string>& moves,
    int next_branch_depth,
    int eval_limit,
    int max_book_ply,
    int think_command_ply,
    int job_id,
    int branch_width,
    const EnqueueBranchTask& enqueue_branch_task)
{
    for (const auto& move : moves)
    {
        auto side_board = bookminer::SfenPosition::from_sfen(current_sfen + " " + std::to_string(current_ply));
        side_board.push_usi(move);
        enqueue_branch_task(BranchTaskSpec{
            "sfen " + side_board.sfen_with_ply(),
            eval_limit,
            max_book_ply,
            think_command_ply,
            job_id,
            branch_width,
            next_branch_depth,
        });
    }
}

std::string flipped_usi_move(const std::string& move)
{
    return bookminer::move16_to_usi(bookminer::flipped_move16(bookminer::move16_from_usi(move)));
}

TaskResult start_thinking_best_line(
    bookminer::BookStore& book,
    bookminer::UsiEngine& engine,
    const std::string& leaf_sfen,
    int leaf_ply,
    int eval_limit,
    int max_book_ply,
    int think_command_ply,
    int branch_width,
    int branch_depth,
    int job_id,
    const EnqueueBranchTask& enqueue_branch_task,
    std::optional<TaskResume>& resume)
{
    std::string current_sfen = leaf_sfen;
    int current_ply = leaf_ply;
    int rest_ply = think_command_ply;
    int rest_branch_depth = branch_depth;
    bool branch_task = branch_width > 0;
    bool branch_extension = false;
    int last_thinking_ply = PlyMin;
    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> visited;
    if (resume && resume->best_line)
    {
        const auto parsed = bookminer::parse_position_command(resume->position_command);
        auto board = bookminer::SfenPosition::from_sfen(parsed.start_sfen_with_ply);
        current_sfen = board.sfen();
        current_ply = board.ply();
        rest_ply = resume->rest_ply;
        rest_branch_depth = resume->rest_branch_depth;
        last_thinking_ply = resume->engine_id == engine.thread_id() ? resume->last_thinking_ply : PlyMin;
        branch_extension = resume->branch_extension;
        if (branch_extension)
            branch_task = false;
        visited = std::move(resume->visited);
        resume.reset();
    }

    while (true)
    {
        if (branch_task && rest_branch_depth <= 0)
        {
            branch_task = false;
            branch_extension = true;
            rest_ply = think_command_ply;
        }
        if (!branch_task && rest_ply <= 0)
            break;

        ThinkOnceResult thought;
        try
        {
            thought = think_sfen_once(book, engine, current_sfen, current_ply, last_thinking_ply, max_book_ply, visited);
        }
        catch (...)
        {
            if (engine.connection_broken())
            {
                forget_failed_position(visited, current_sfen);
                resume = TaskResume{true, "sfen " + current_sfen + " " + std::to_string(current_ply),
                                    rest_ply, rest_branch_depth, last_thinking_ply, engine.thread_id(), std::move(visited), branch_extension};
            }
            throw;
        }
        if (thought.status == ThinkOnceStatus::Deferred)
        {
            resume = TaskResume{true, "sfen " + current_sfen + " " + std::to_string(current_ply),
                                rest_ply, rest_branch_depth, last_thinking_ply, engine.thread_id(), std::move(visited), branch_extension};
            return TaskResult::Deferred;
        }
        if (thought.status != ThinkOnceStatus::Position)
            return TaskResult::Done;
        current_sfen = thought.sfen;
        const auto candidate_moves = thinking_candidate_moves(
            thought.position,
            eval_limit,
            branch_task ? std::optional<std::size_t>(static_cast<std::size_t>(branch_width)) : std::nullopt,
            book, current_sfen + " " + std::to_string(current_ply));

        if (candidate_moves.empty())
            return TaskResult::Done;

        if (branch_task)
        {
            const int next_branch_depth = rest_branch_depth - 1;
            std::vector<std::string> side_moves;
            if (candidate_moves.size() > 1)
                side_moves.assign(candidate_moves.begin() + 1, candidate_moves.end());
            enqueue_branch_tasks(
                current_sfen,
                current_ply,
                side_moves,
                next_branch_depth,
                eval_limit,
                max_book_ply,
                think_command_ply,
                job_id,
                branch_width,
                enqueue_branch_task);

            auto board = bookminer::SfenPosition::from_sfen(current_sfen + " " + std::to_string(current_ply));
            board.push_usi(candidate_moves.front());
            current_sfen = board.sfen();
            current_ply = board.ply();
            rest_branch_depth = next_branch_depth;
            continue;
        }

        auto board = bookminer::SfenPosition::from_sfen(current_sfen + " " + std::to_string(current_ply));
        board.push_usi(candidate_moves.front());
        current_sfen = board.sfen();
        current_ply = board.ply();
        --rest_ply;
    }
    return TaskResult::Done;
}

bool position_command_hits_searching_position(
    const bookminer::BookStore& book,
    const bookminer::ParsedPositionCommand& parsed,
    int max_book_ply)
{
    auto board = bookminer::SfenPosition::from_sfen(parsed.start_sfen_with_ply);
    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> path_visited;
    bool stopped = false;

    for (const auto& move : parsed.moves)
    {
        const auto current_sfen = board.sfen();
        const int ply = board.ply();
        const auto key = bookminer::PackedSfen::from_sfen(current_sfen);
        const auto flipped_key = key.flipped();
        if (path_visited.find(key) != path_visited.end() || path_visited.find(flipped_key) != path_visited.end())
        {
            stopped = true;
            break;
        }
        path_visited.insert(key);
        path_visited.insert(flipped_key);
        if (ply >= max_book_ply)
        {
            stopped = true;
            break;
        }
        if (book.is_searching(current_sfen, bookminer::flipped_sfen(current_sfen)))
            return true;
        board.push_usi(move);
    }

    if (!stopped)
    {
        const auto leaf_sfen = board.sfen();
        const int leaf_ply = board.ply();
        const auto key = bookminer::PackedSfen::from_sfen(leaf_sfen);
        const auto flipped_key = key.flipped();
        if (path_visited.find(key) == path_visited.end()
            && path_visited.find(flipped_key) == path_visited.end()
            && leaf_ply < max_book_ply
            && book.is_searching(leaf_sfen, bookminer::flipped_sfen(leaf_sfen)))
        {
            return true;
        }
    }

    return false;
}

TaskResult process_position_command(
    bookminer::BookStore& book,
    bookminer::UsiEngine& engine,
    const std::string& position_command,
    int eval_limit,
    int max_book_ply,
    int think_command_ply,
    int branch_width,
    int branch_depth,
    int job_id,
    const EnqueueBranchTask& enqueue_branch_task,
    std::optional<TaskResume>& resume)
{
    if (resume && resume->best_line)
    {
        engine.usinewgame([](const std::string& message) { log_line(message); });
        return start_thinking_best_line(book, engine, {}, 0, eval_limit, max_book_ply,
            think_command_ply, branch_width, branch_depth, job_id, enqueue_branch_task, resume);
    }
    const auto parsed = bookminer::parse_position_command(resume ? resume->position_command : position_command);
    if (position_command_hits_searching_position(book, parsed, max_book_ply))
        return TaskResult::Deferred;

    auto board = bookminer::SfenPosition::from_sfen(parsed.start_sfen_with_ply);

    engine.usinewgame([](const std::string& message) {
        log_line(message);
    });

    int rest_branch_depth = resume ? resume->rest_branch_depth : branch_depth;
    const bool branch_task = branch_width > 0;
    int last_thinking_ply = resume && resume->engine_id == engine.thread_id() ? resume->last_thinking_ply : PlyMin;
    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> visited;
    if (resume)
        visited = std::move(resume->visited);
    resume.reset();

    for (std::size_t move_index = 0; move_index < parsed.moves.size(); ++move_index)
    {
        const auto& move = parsed.moves[move_index];
        const auto current_sfen = board.sfen();
        const int ply = board.ply();

        auto current_lookup = find_book_position_with_flip(book, current_sfen);
        if (has_considered(current_lookup.position))
        {
            const auto current_ply = static_cast<std::uint16_t>(std::clamp(ply, 0, 65535));
            if (current_ply < current_lookup.position->ply)
            {
                auto key = bookminer::PackedSfen::from_sfen(current_sfen);
                if (current_lookup.flipped)
                    key.flip();
                (void)book.update_position_ply(key, current_ply);
                current_lookup.position->ply = current_ply;
            }
        }
        if (!has_considered(current_lookup.position))
        {
            ThinkOnceResult thought;
            try
            {
                thought = think_sfen_once(book, engine, current_sfen, ply, last_thinking_ply, max_book_ply, visited);
            }
            catch (...)
            {
                if (engine.connection_broken())
                {
                    forget_failed_position(visited, current_sfen);
                    std::string remaining = "sfen " + board.sfen_with_ply() + " moves";
                    for (std::size_t i = move_index; i < parsed.moves.size(); ++i)
                        remaining += " " + parsed.moves[i];
                    resume = TaskResume{false, std::move(remaining), 0, rest_branch_depth,
                                        last_thinking_ply, engine.thread_id(), std::move(visited)};
                }
                throw;
            }
            if (thought.status == ThinkOnceStatus::Deferred)
            {
                std::string remaining = "sfen " + board.sfen_with_ply() + " moves";
                for (std::size_t i = move_index; i < parsed.moves.size(); ++i)
                    remaining += " " + parsed.moves[i];
                resume = TaskResume{false, std::move(remaining), 0, rest_branch_depth,
                                    last_thinking_ply, engine.thread_id(), std::move(visited)};
                return TaskResult::Deferred;
            }
            if (thought.status != ThinkOnceStatus::Position)
                return TaskResult::Done;
            current_lookup.position = thought.position;
            current_lookup.flipped = thought.sfen != current_sfen;

            if (branch_task && thought.searched && rest_branch_depth > 0)
            {
                const auto candidate_moves = thinking_candidate_moves(
                    thought.position,
                    eval_limit,
                    static_cast<std::size_t>(branch_width),
                    book, thought.sfen + " " + std::to_string(ply));
                if (candidate_moves.empty())
                {
                    rest_branch_depth = 0;
                }
                else
                {
                    const std::string inline_move = current_lookup.flipped ? flipped_usi_move(move) : move;
                    const int next_branch_depth = rest_branch_depth - 1;
                    std::vector<std::string> side_moves;
                    for (const auto& candidate_move : candidate_moves)
                    {
                        if (candidate_move != inline_move)
                            side_moves.push_back(candidate_move);
                    }
                    enqueue_branch_tasks(
                        thought.sfen,
                        ply,
                        side_moves,
                        next_branch_depth,
                        eval_limit,
                        max_book_ply,
                        think_command_ply,
                        job_id,
                        branch_width,
                        enqueue_branch_task);
                    rest_branch_depth = std::find(candidate_moves.begin(), candidate_moves.end(), inline_move) != candidate_moves.end()
                        ? next_branch_depth
                        : 0;
                }
            }
        }

        auto lookahead = board;
        lookahead.push_usi(move);
        const auto next_sfen = lookahead.sfen();
        const auto next_lookup = find_considered_book_position_with_flip(book, next_sfen);

        if (!has_considered(next_lookup.position) && current_lookup.position.has_value())
        {
            if (peta_cut_by_eval_limit(*current_lookup.position, current_lookup.flipped,
                                       board.sfen_with_ply(), eval_limit, &book, true))
                return TaskResult::Done;
        }

        board.push_usi(move);
    }

    // Even when book_extend_ply is zero, an already stored leaf may have
    // been reached here by a shorter path than its recorded ply.
    const auto leaf_key = bookminer::PackedSfen::from_sfen(board.sfen());
    const auto leaf_ply = static_cast<std::uint16_t>(std::clamp(board.ply(), 0, 65535));
    if (!book.update_position_ply(leaf_key, leaf_ply))
        (void)book.update_position_ply(leaf_key.flipped(), leaf_ply);

    return start_thinking_best_line(
        book,
        engine,
        board.sfen(),
        board.ply(),
        eval_limit,
        max_book_ply,
        think_command_ply,
        branch_width,
        rest_branch_depth,
        job_id,
        enqueue_branch_task,
        resume);
}

struct PositionCommandEntry {
    std::string position_command;
    std::optional<int> book_extend_ply;
    std::optional<int> eval_limit;
    std::optional<int> max_book_ply;
};

int optional_int_rank(const std::optional<int>& value)
{
    return value.value_or(-1);
}

std::optional<int> max_optional_int(const std::optional<int>& lhs, const std::optional<int>& rhs)
{
    return optional_int_rank(lhs) >= optional_int_rank(rhs) ? lhs : rhs;
}

std::string trim_metadata_copy(std::string text)
{
    text.erase(text.begin(), std::find_if(text.begin(), text.end(), [](unsigned char ch) {
        return !std::isspace(ch);
    }));
    while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back())))
        text.pop_back();
    return text;
}

PositionCommandEntry parse_position_command_entry(const std::string& line)
{
    std::vector<std::string> parts;
    std::string part;
    std::istringstream stream(line);
    while (std::getline(stream, part, ','))
        parts.push_back(trim_metadata_copy(part));

    if (parts.empty() || parts[0].empty())
        throw std::runtime_error("empty position command");

    PositionCommandEntry entry{parts[0], std::nullopt, std::nullopt, std::nullopt};
    for (std::size_t i = 1; i < parts.size(); ++i)
    {
        const auto& meta = parts[i];
        if (meta.empty())
            continue;
        const auto equal_pos = meta.find('=');
        if (equal_pos == std::string::npos)
            throw std::runtime_error("invalid metadata: " + meta);
        const std::string key = trim_metadata_copy(meta.substr(0, equal_pos));
        const std::string value = trim_metadata_copy(meta.substr(equal_pos + 1));
        if (key == "book_extend_ply")
        {
            if (is_none_argument(value))
            {
                entry.book_extend_ply = std::nullopt;
            }
            else
            {
                const int parsed = parse_integer(value);
                if (parsed < 0)
                    throw std::runtime_error("book_extend_ply must be non-negative integer or None");
                entry.book_extend_ply = parsed;
            }
        }
        else if (key == "eval_limit")
        {
            if (is_none_argument(value))
            {
                entry.eval_limit = std::nullopt;
            }
            else
            {
                const int parsed = parse_integer(value);
                if (parsed < 0)
                    throw std::runtime_error("eval_limit must be non-negative integer or None");
                entry.eval_limit = parsed;
            }
        }
        else if (key == "game_ply_limit")
        {
            if (is_none_argument(value))
            {
                entry.max_book_ply = std::nullopt;
            }
            else
            {
                const int parsed = parse_integer(value);
                if (parsed <= 0)
                    throw std::runtime_error("game_ply_limit must be positive integer or None");
                entry.max_book_ply = parsed;
            }
        }
        else
            throw std::runtime_error("unknown metadata: " + key);
    }
    return entry;
}

std::string format_position_command_entry(const PositionCommandEntry& entry)
{
    std::vector<std::string> metadata;
    if (entry.book_extend_ply.has_value())
        metadata.push_back("book_extend_ply=" + std::to_string(*entry.book_extend_ply));
    if (entry.eval_limit.has_value())
        metadata.push_back("eval_limit=" + std::to_string(*entry.eval_limit));
    if (entry.max_book_ply.has_value())
        metadata.push_back("game_ply_limit=" + std::to_string(*entry.max_book_ply));
    if (metadata.empty())
        return entry.position_command;

    std::string result = entry.position_command + ", " + metadata.front();
    for (std::size_t i = 1; i < metadata.size(); ++i)
        result += ", " + metadata[i];
    return result;
}

void add_position_command_entry(
    std::vector<PositionCommandEntry>& out,
    std::unordered_map<std::string, std::size_t>& indexes,
    const std::string& position_command,
    std::optional<int> book_extend_ply = std::nullopt,
    std::optional<int> eval_limit = std::nullopt,
    std::optional<int> max_book_ply = std::nullopt)
{
    auto it = indexes.find(position_command);
    if (it == indexes.end())
    {
        indexes[position_command] = out.size();
        out.push_back(PositionCommandEntry{position_command, book_extend_ply, eval_limit, max_book_ply});
        return;
    }

    auto& old = out[it->second];
    old.book_extend_ply = max_optional_int(old.book_extend_ply, book_extend_ply);
    old.eval_limit = max_optional_int(old.eval_limit, eval_limit);
    old.max_book_ply = max_optional_int(old.max_book_ply, max_book_ply);
}

std::vector<PositionCommandEntry> read_position_commands_file(
    const std::filesystem::path& path, int eval_limit, int max_book_ply, int think_command_ply)
{
    std::ifstream in(path);
    if (!in)
        throw std::runtime_error("failed to open think_sfens file: " + path.string());

    std::vector<PositionCommandEntry> commands;
    std::unordered_map<std::string, std::size_t> indexes;
    std::string line;
    bool first_line = true;
    std::size_t line_number = 0;
    while (std::getline(in, line))
    {
        ++line_number;
        if (first_line && line.size() >= 3
            && static_cast<unsigned char>(line[0]) == 0xef
            && static_cast<unsigned char>(line[1]) == 0xbb
            && static_cast<unsigned char>(line[2]) == 0xbf)
            line.erase(0, 3);
        first_line = false;
        line = trim_metadata_copy(std::move(line));
        if (line.empty() || line[0] == '#')
            continue;
        PositionCommandEntry entry;
        try
        {
            entry = parse_position_command_entry(line);
        }
        catch (const std::exception& ex)
        {
            throw std::runtime_error("invalid think_sfens entry at " + path.string() + ":"
                                     + std::to_string(line_number) + " : " + ex.what());
        }
        add_position_command_entry(
            commands,
            indexes,
            entry.position_command,
            entry.book_extend_ply.value_or(think_command_ply),
            entry.eval_limit.value_or(eval_limit),
            entry.max_book_ply.value_or(max_book_ply));
    }
    if (in.bad())
        throw std::runtime_error("failed to read think_sfens file: " + path.string());
    return commands;
}

template <typename Getter>
std::string summarize_effective_position_command_value(
    const std::vector<PositionCommandEntry>& commands,
    int default_value,
    Getter getter)
{
    if (commands.empty())
        return std::to_string(default_value);

    std::optional<int> first_value;
    for (const auto& command : commands)
    {
        const int value = getter(command).value_or(default_value);
        if (!first_value.has_value())
        {
            first_value = value;
            continue;
        }
        if (*first_value != value)
            return "mixed";
    }
    return std::to_string(*first_value);
}

struct Task {
    std::string position_command;
    int eval_limit = 0;
    int max_book_ply = 0;
    int think_command_ply = ThinkCommandPly;
    int job_id = 0;
    int defer_count = 0;
    int branch_width = 0;
    int branch_depth = 0;
    std::optional<TaskResume> resume;
};

class TaskQueue {
public:
    bool push(Task task)
    {
        {
            std::scoped_lock lock(mutex_);
            if (stopping_ || cancelled_.count(task.job_id))
                return false;
            const int job_id = task.job_id;
            auto it = jobs_.find(job_id);
            if (it == jobs_.end())
            {
                it = jobs_.emplace(job_id, std::deque<Task>{}).first;
                job_order_.push_back(job_id);
            }
            it->second.push_back(std::move(task));
            ++count_;
        }
        cv_.notify_one();
        return true;
    }

    bool push_deferred(Task task)
    {
        return push(std::move(task));
    }

    std::size_t control(int job_id, const std::string& action)
    {
        std::scoped_lock lock(mutex_);
        if (action == "pause") paused_.insert(job_id);
        else if (action == "resume") paused_.erase(job_id);
        else if (action == "delete") {
            cancelled_.insert(job_id);
            paused_.erase(job_id);
            auto it = jobs_.find(job_id);
            const auto removed = it == jobs_.end() ? 0 : it->second.size();
            if (it != jobs_.end()) jobs_.erase(it);
            job_order_.erase(std::remove(job_order_.begin(), job_order_.end(), job_id), job_order_.end());
            count_ -= removed;
            cv_.notify_all();
            return removed;
        }
        cv_.notify_all();
        return 0;
    }

    std::optional<Task> pop()
    {
        std::unique_lock lock(mutex_);
        cv_.wait(lock, [&] {
            return stopping_ || std::any_of(job_order_.begin(), job_order_.end(),
                [&](int id) { return !paused_.count(id); });
        });
        if (count_ == 0)
            return std::nullopt;

        const auto available = std::find_if(job_order_.begin(), job_order_.end(),
            [&](int id) { return stopping_ || !paused_.count(id); });
        const int job_id = *available;
        job_order_.erase(available);

        auto job_it = jobs_.find(job_id);
        if (job_it == jobs_.end() || job_it->second.empty())
            throw std::runtime_error("TaskQueue job order is inconsistent.");

        auto task = std::move(job_it->second.front());
        job_it->second.pop_front();
        --count_;
        if (job_it->second.empty())
            jobs_.erase(job_it);
        else
            job_order_.push_back(job_id);
        return task;
    }

    std::size_t size() const
    {
        std::scoped_lock lock(mutex_);
        return count_;
    }

    void stop(bool discard_pending)
    {
        {
            std::scoped_lock lock(mutex_);
            stopping_ = true;
            if (discard_pending)
            {
                jobs_.clear();
                job_order_.clear();
                count_ = 0;
            }
        }
        cv_.notify_all();
    }

private:
    mutable std::mutex mutex_;
    std::condition_variable cv_;
    std::unordered_map<int, std::deque<Task>> jobs_;
    std::deque<int> job_order_;
    std::unordered_set<int> paused_;
    std::unordered_set<int> cancelled_;
    std::size_t count_ = 0;
    bool stopping_ = false;
};

class TaskWorkers {
public:
    TaskWorkers(
        bookminer::BookStore& book,
        std::vector<std::unique_ptr<bookminer::UsiEngine>>& engines,
        int)
        : book_(book)
        , engines_(engines)
    {
    }

    ~TaskWorkers()
    {
        stop(true);
    }

    void start()
    {
        active_workers_.store(static_cast<int>(engines_.size()));
        for (auto& engine : engines_)
        {
            auto alive = std::make_shared<std::atomic<bool>>(true);
            worker_alive_.push_back(alive);
            threads_.emplace_back([this, engine_ptr = engine.get(), alive] {
                worker_loop(*engine_ptr, *alive);
            });
        }
        progress_reporter_thread_ = std::thread([this] {
            progress_reporter_loop();
        });
    }

    int enqueue_position_commands(
        const std::filesystem::path& path,
        int eval_limit,
        int max_book_ply,
        int think_command_ply,
        int branch_width = 0,
        int branch_depth = 0)
    {
        const auto commands = read_position_commands_file(path, eval_limit, max_book_ply, think_command_ply);
        if (!commands.empty() && engines_.empty())
            throw std::runtime_error("no search engines are configured");
        if (!commands.empty() && active_workers_.load() == 0)
            throw std::runtime_error("no search engines are available");
        const int job_id = next_job_id_.fetch_add(1);
        const auto added = commands.size();
        const std::string job_eval_limit = summarize_effective_position_command_value(
            commands,
            eval_limit,
            [](const PositionCommandEntry& command) {
                return command.eval_limit;
            });
        const std::string job_game_ply_limit = summarize_effective_position_command_value(
            commands,
            max_book_ply,
            [](const PositionCommandEntry& command) {
                return command.max_book_ply;
            });
        const std::string job_book_extend_ply = summarize_effective_position_command_value(
            commands,
            think_command_ply,
            [](const PositionCommandEntry& command) {
                return command.book_extend_ply;
            });
        const std::string job_branch = std::to_string(branch_width) + "-" + std::to_string(branch_depth);

        std::size_t total_taken = 0;
        std::size_t total_enqueued = 0;
        {
            std::scoped_lock lock(progress_mutex_);
            JobProgress progress;
            progress.total = added;
            progress.eval_limit = job_eval_limit;
            progress.game_ply_limit = job_game_ply_limit;
            progress.book_extend_ply = job_book_extend_ply;
            progress.branch = job_branch;
            progress.last_reported_total = added;
            jobs_[job_id] = std::move(progress);
            total_enqueued_ += added;
            total_taken = total_taken_;
            total_enqueued = total_enqueued_;
        }

        log_line("(" + std::to_string(job_id) + ") put position commands , path = " + path.string()
            + " , eval_limit = " + std::to_string(eval_limit)
            + ", max_book_ply = " + std::to_string(max_book_ply)
            + ", think_command_ply = " + std::to_string(think_command_ply)
            + ", branch = " + job_branch);
        log_line("(" + std::to_string(job_id) + ") read " + std::to_string(added) + " position commands.");
        log_line("[TaskQueueStart] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
            + " job=" + std::to_string(job_id)
            + " job_progress=0/" + std::to_string(added)
            + " job_remaining=" + std::to_string(added)
            + " added=" + std::to_string(added)
            + " remaining=" + std::to_string(total_enqueued - total_taken)
            + " path=" + path.string()
            + " deferred=0"
            + " eval_limit=" + job_eval_limit
            + " game_ply_limit=" + job_game_ply_limit
            + " book_extend_ply=" + job_book_extend_ply
            + " branch=" + job_branch);

        for (const auto& command : commands)
        {
            const int task_eval_limit = command.eval_limit.value_or(eval_limit);
            const int task_max_book_ply = command.max_book_ply.value_or(max_book_ply);
            const int task_think_command_ply = command.book_extend_ply.value_or(think_command_ply);
            queue_.push(Task{
                command.position_command,
                task_eval_limit,
                task_max_book_ply,
                task_think_command_ply,
                job_id,
                0,
                branch_width,
                branch_depth,
                std::nullopt,
            });
        }

        if (commands.empty())
            report_task_queue_done(job_id);

        return job_id;
    }

    void control_job(int job_id, const std::string& action)
    {
        std::scoped_lock lock(progress_mutex_);
        auto it = jobs_.find(job_id);
        if (it == jobs_.end() || it->second.done_reported || it->second.cancelled)
        {
            log_line("[JobControlError] job=" + std::to_string(job_id) + " reason=not-active");
            return;
        }
        auto& job = it->second;
        const auto removed = queue_.control(job_id, action);
        if (action == "delete")
        {
            job.cancelled = true;
            job.taken += removed;
            total_taken_ += removed;
        }
        log_line("[JobControl] job=" + std::to_string(job_id) + " state=" + action
                 + " removed=" + std::to_string(removed));
        if (removed)
        {
            log_line("[TaskQueueProgress] " + std::to_string(total_taken_) + "/" + std::to_string(total_enqueued_)
                + " job=" + std::to_string(job_id) + " job_progress=" + std::to_string(job.taken) + "/" + std::to_string(job.total)
                + " job_remaining=" + std::to_string(job.total - job.taken)
                + " remaining=" + std::to_string(total_enqueued_ - total_taken_));
            if (job.taken == job.total)
            {
                job.done_reported = true;
                log_line("[TaskQueueJobDone] " + std::to_string(total_taken_) + "/" + std::to_string(total_enqueued_)
                    + " job=" + std::to_string(job_id) + " job_progress=" + std::to_string(job.taken) + "/" + std::to_string(job.total)
                    + " job_remaining=0 remaining=" + std::to_string(total_enqueued_ - total_taken_));
                if (total_taken_ == total_enqueued_)
                    log_line("[TaskQueueDone] " + std::to_string(total_taken_) + "/" + std::to_string(total_enqueued_)
                        + " job=" + std::to_string(job_id) + " job_progress=" + std::to_string(job.taken) + "/" + std::to_string(job.total)
                        + " job_remaining=0 remaining=0");
            }
        }
    }

    void stop(bool discard_pending)
    {
        if (stopped_.exchange(true))
            return;

        progress_reporter_cv_.notify_all();
        queue_.stop(discard_pending);
        for (auto& thread : threads_)
        {
            if (thread.joinable())
                thread.join();
        }
        if (progress_reporter_thread_.joinable())
            progress_reporter_thread_.join();
    }

private:
    struct JobProgress {
        bool cancelled = false;
        std::size_t total = 0;
        std::size_t taken = 0;
        std::size_t last_reported_taken = 0;
        std::size_t last_reported_total = 0;
        std::size_t deferred = 0;
        std::size_t failed = 0;
        std::size_t last_reported_deferred = 0;
        bool done_reported = false;
        std::string eval_limit;
        std::string game_ply_limit;
        std::string book_extend_ply;
        std::string branch;
    };

    struct ProgressReport {
        int job_id = 0;
        std::size_t total_taken = 0;
        std::size_t total_enqueued = 0;
        std::size_t job_taken = 0;
        std::size_t job_total = 0;
        std::size_t job_remaining = 0;
        std::size_t remaining = 0;
        std::size_t deferred = 0;
        std::string eval_limit;
        std::string game_ply_limit;
        std::string book_extend_ply;
        std::string branch;
    };

    void enqueue_branch_task(BranchTaskSpec spec)
    {
        std::scoped_lock lock(progress_mutex_);
        auto it = jobs_.find(spec.job_id);
        if (it != jobs_.end() && it->second.cancelled) return;
        if (!queue_.push(Task{
            std::move(spec.position_command),
            spec.eval_limit,
            spec.max_book_ply,
            spec.think_command_ply,
            spec.job_id,
            0,
            spec.branch_width,
            spec.branch_depth,
            std::nullopt,
        })) return;
        ++total_enqueued_;
        if (it != jobs_.end()) ++it->second.total;
    }

    void retire_worker(bookminer::UsiEngine& engine, std::atomic<bool>& alive)
    {
        std::scoped_lock lock(engine_alive_mutex_);
        if (!alive.exchange(false))
            return;
        const int remaining = active_workers_.fetch_sub(1) - 1;
        log_line("[EngineAlive] " + std::to_string(remaining) + "/" + std::to_string(engines_.size())
                 + " dead=" + std::to_string(engine.thread_id()));
    }

    void worker_loop(bookminer::UsiEngine& engine, std::atomic<bool>& alive)
    {
        while (true)
        {
            auto task = queue_.pop();
            if (!task.has_value())
                return;

            TaskResult result = TaskResult::Done;
            bool failed = false;
            try
            {
                result = process_position_command(
                    book_,
                    engine,
                    task->position_command,
                    task->eval_limit,
                    task->max_book_ply,
                    task->think_command_ply,
                    task->branch_width,
                    task->branch_depth,
                    task->job_id,
                    [this](BranchTaskSpec spec) {
                        enqueue_branch_task(std::move(spec));
                    }, task->resume);
            }
            catch (const std::exception& ex)
            {
                if (engine.connection_broken())
                {
                    log_line("[EngineUnavailable] engine=" + std::to_string(engine.thread_id())
                        + " job=" + std::to_string(task->job_id) + " : " + ex.what());
                    retire_worker(engine, alive);
                    if (!stopped_.load() && !queue_.push(*task))
                        report_task_done(*task);
                    return;
                }
                failed = true;
                log_line(std::string("Exception : task failed, job=") + std::to_string(task->job_id)
                    + " engine=" + std::to_string(engine.thread_id())
                    + " position=" + task->position_command + " : " + ex.what());
            }
            if (result == TaskResult::Deferred)
            {
                defer_task(std::move(*task));
                continue;
            }
            report_task_done(*task, failed);
        }
    }

    void defer_task(Task task)
    {
        if (stopped_.load())
            return;
        if (task.defer_count < std::numeric_limits<int>::max())
            ++task.defer_count;
        {
            std::scoped_lock lock(progress_mutex_);
            auto it = jobs_.find(task.job_id);
            if (it != jobs_.end())
                ++it->second.deferred;
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(TaskDeferSleepMilliseconds));
        if (!stopped_.load() && !queue_.push_deferred(task))
            report_task_done(task);
    }

    void progress_reporter_loop()
    {
        std::unique_lock lock(progress_reporter_mutex_);
        auto next_progress = std::chrono::steady_clock::now() + std::chrono::seconds(10);
        while (!stopped_.load())
        {
            if (progress_reporter_cv_.wait_for(lock, std::chrono::seconds(1), [this] {
                    return stopped_.load();
                }))
            {
                break;
            }
            for (std::size_t i = 0; i < engines_.size(); ++i)
                if (worker_alive_[i]->load() && !engines_[i]->process_alive())
                    retire_worker(*engines_[i], *worker_alive_[i]);
            if (std::chrono::steady_clock::now() >= next_progress)
            {
                report_periodic_task_progress();
                next_progress = std::chrono::steady_clock::now() + std::chrono::seconds(10);
            }
        }
    }

    void report_task_done(const Task& task, bool failed = false)
    {
        std::size_t total_taken = 0;
        std::size_t total_enqueued = 0;
        std::size_t job_taken = 0;
        std::size_t job_total = 0;
        bool should_report = false;
        bool should_report_job_done = false;
        std::string job_eval_limit;
        std::string job_game_ply_limit;
        std::string job_book_extend_ply;
        std::string job_branch;
        std::size_t job_deferred = 0;
        std::size_t job_failed = 0;
        std::size_t total_failed = 0;

        {
            std::scoped_lock lock(progress_mutex_);
            ++total_taken_;
            total_taken = total_taken_;
            total_enqueued = total_enqueued_;

            auto& job = jobs_[task.job_id];
            if (job.eval_limit.empty())
                job.eval_limit = std::to_string(task.eval_limit);
            if (job.game_ply_limit.empty())
                job.game_ply_limit = std::to_string(task.max_book_ply);
            if (job.book_extend_ply.empty())
                job.book_extend_ply = std::to_string(task.think_command_ply);
            if (job.branch.empty())
                job.branch = std::to_string(task.branch_width) + "-" + std::to_string(task.branch_depth);
            ++job.taken;
            if (failed)
            {
                ++job.failed;
                ++total_failed_;
            }
            job_failed = job.failed;
            total_failed = total_failed_;
            job_taken = job.taken;
            job_total = job.total;
            job_eval_limit = job.eval_limit;
            job_game_ply_limit = job.game_ply_limit;
            job_book_extend_ply = job.book_extend_ply;
            job_branch = job.branch;
            job_deferred = job.deferred;
            should_report_job_done = job.total > 0 && job.taken >= job.total && !job.done_reported;
            if (should_report_job_done)
                job.done_reported = true;

            const auto remaining = total_enqueued > total_taken ? total_enqueued - total_taken : 0;
            if (remaining == 0 || should_report_job_done)
            {
                should_report = true;
                job.last_reported_taken = job.taken;
                job.last_reported_total = job.total;
                job.last_reported_deferred = job.deferred;
                last_task_progress_report_ = std::chrono::steady_clock::now();
            }
        }

        if (!should_report)
            return;

        const auto remaining = total_enqueued > total_taken ? total_enqueued - total_taken : 0;
        const auto job_remaining = job_total > job_taken ? job_total - job_taken : 0;
        log_line("[TaskQueueProgress] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
            + " job=" + std::to_string(task.job_id)
            + " job_progress=" + std::to_string(job_taken) + "/" + std::to_string(job_total)
            + " job_remaining=" + std::to_string(job_remaining)
            + " remaining=" + std::to_string(remaining)
            + " deferred=" + std::to_string(job_deferred)
            + " failed=" + std::to_string(job_failed)
            + " total_failed=" + std::to_string(total_failed)
            + " eval_limit=" + job_eval_limit
            + " game_ply_limit=" + job_game_ply_limit
            + " book_extend_ply=" + job_book_extend_ply
            + " branch=" + job_branch);

        if (should_report_job_done)
        {
            log_line("[TaskQueueJobDone] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
                + " job=" + std::to_string(task.job_id)
                + " job_progress=" + std::to_string(job_taken) + "/" + std::to_string(job_total)
                + " job_remaining=0"
                + " remaining=" + std::to_string(remaining)
                + " deferred=" + std::to_string(job_deferred)
                + " failed=" + std::to_string(job_failed)
                + " total_failed=" + std::to_string(total_failed)
                + " eval_limit=" + job_eval_limit
                + " game_ply_limit=" + job_game_ply_limit
                + " book_extend_ply=" + job_book_extend_ply
                + " branch=" + job_branch);
        }

        if (remaining == 0)
        {
            log_line("[TaskQueueDone] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
                + " job=" + std::to_string(task.job_id)
                + " job_progress=" + std::to_string(job_taken) + "/" + std::to_string(job_total)
                + " job_remaining=" + std::to_string(job_remaining)
                + " remaining=0"
                + " deferred=" + std::to_string(job_deferred)
                + " failed=" + std::to_string(job_failed)
                + " total_failed=" + std::to_string(total_failed)
                + " eval_limit=" + job_eval_limit
                + " game_ply_limit=" + job_game_ply_limit
                + " book_extend_ply=" + job_book_extend_ply
                + " branch=" + job_branch);
        }
    }

    void report_periodic_task_progress()
    {
        std::vector<ProgressReport> reports;
        {
            std::scoped_lock lock(progress_mutex_);
            const auto remaining = total_enqueued_ > total_taken_ ? total_enqueued_ - total_taken_ : 0;
            for (auto& [job_id, job] : jobs_)
            {
                if (job.done_reported)
                    continue;
                if (job.taken == job.last_reported_taken
                    && job.total == job.last_reported_total
                    && job.deferred == job.last_reported_deferred)
                    continue;

                ProgressReport report;
                report.job_id = job_id;
                report.total_taken = total_taken_;
                report.total_enqueued = total_enqueued_;
                report.job_taken = job.taken;
                report.job_total = job.total;
                report.job_remaining = job.total > job.taken ? job.total - job.taken : 0;
                report.remaining = remaining;
                report.deferred = job.deferred;
                report.eval_limit = job.eval_limit;
                report.game_ply_limit = job.game_ply_limit;
                report.book_extend_ply = job.book_extend_ply;
                report.branch = job.branch;
                reports.push_back(std::move(report));
                job.last_reported_taken = job.taken;
                job.last_reported_total = job.total;
                job.last_reported_deferred = job.deferred;
            }
            if (!reports.empty())
                last_task_progress_report_ = std::chrono::steady_clock::now();
        }

        for (const auto& report : reports)
        {
            log_line("[TaskQueueProgress] " + std::to_string(report.total_taken) + "/" + std::to_string(report.total_enqueued)
                + " job=" + std::to_string(report.job_id)
                + " job_progress=" + std::to_string(report.job_taken) + "/" + std::to_string(report.job_total)
                + " job_remaining=" + std::to_string(report.job_remaining)
                + " remaining=" + std::to_string(report.remaining)
                + " deferred=" + std::to_string(report.deferred)
                + " eval_limit=" + report.eval_limit
                + " game_ply_limit=" + report.game_ply_limit
                + " book_extend_ply=" + report.book_extend_ply
                + " branch=" + report.branch);
        }
    }

    void report_task_queue_done(int job_id)
    {
        std::size_t total_taken = 0;
        std::size_t total_enqueued = 0;
        std::string job_eval_limit = "-";
        std::string job_game_ply_limit = "-";
        std::string job_book_extend_ply = "-";
        std::string job_branch = "-";
        std::size_t job_deferred = 0;
        {
            std::scoped_lock lock(progress_mutex_);
            total_taken = total_taken_;
            total_enqueued = total_enqueued_;
            const auto it = jobs_.find(job_id);
            if (it != jobs_.end())
            {
                job_eval_limit = it->second.eval_limit.empty() ? "-" : it->second.eval_limit;
                job_game_ply_limit = it->second.game_ply_limit.empty() ? "-" : it->second.game_ply_limit;
                job_book_extend_ply = it->second.book_extend_ply.empty() ? "-" : it->second.book_extend_ply;
                job_branch = it->second.branch.empty() ? "-" : it->second.branch;
                job_deferred = it->second.deferred;
            }
        }
        log_line("[TaskQueueJobDone] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
            + " job=" + std::to_string(job_id)
            + " job_progress=0/0 job_remaining=0 remaining=" + std::to_string(total_enqueued - total_taken)
            + " deferred=" + std::to_string(job_deferred)
            + " eval_limit=" + job_eval_limit
            + " game_ply_limit=" + job_game_ply_limit
            + " book_extend_ply=" + job_book_extend_ply
            + " branch=" + job_branch);
        if (total_taken >= total_enqueued)
        {
            log_line("[TaskQueueDone] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
                + " job=" + std::to_string(job_id)
                + " job_progress=0/0 job_remaining=0 remaining=0"
                + " deferred=" + std::to_string(job_deferred)
                + " eval_limit=" + job_eval_limit
                + " game_ply_limit=" + job_game_ply_limit
                + " book_extend_ply=" + job_book_extend_ply
                + " branch=" + job_branch);
        }
    }

    bookminer::BookStore& book_;
    std::vector<std::unique_ptr<bookminer::UsiEngine>>& engines_;
    TaskQueue queue_;
    std::vector<std::thread> threads_;
    std::vector<std::shared_ptr<std::atomic<bool>>> worker_alive_;
    std::thread progress_reporter_thread_;
    std::atomic<int> active_workers_{0};
    std::mutex engine_alive_mutex_;
    std::atomic<int> next_job_id_{1};
    std::atomic<bool> stopped_{false};
    std::mutex progress_mutex_;
    std::mutex progress_reporter_mutex_;
    std::condition_variable progress_reporter_cv_;
    std::map<int, JobProgress> jobs_;
    std::size_t total_enqueued_ = 0;
    std::size_t total_taken_ = 0;
    std::size_t total_failed_ = 0;
    std::chrono::steady_clock::time_point last_task_progress_report_{};
};

std::string append_position_move(std::string position_command, const std::string& move)
{
    while (!position_command.empty() && std::isspace(static_cast<unsigned char>(position_command.back())))
        position_command.pop_back();

    if (position_command.find(" moves ") != std::string::npos)
        return position_command + " " + move;
    return position_command + " moves " + move;
}

std::string trim_copy(std::string text)
{
    text.erase(text.begin(), std::find_if(text.begin(), text.end(), [](unsigned char ch) {
        return !std::isspace(ch);
    }));
    while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back())))
        text.pop_back();
    return text;
}

std::string base_position_command(const std::string& position_command)
{
    std::string text = trim_copy(position_command);
    if (text.rfind("position ", 0) == 0)
        text.erase(0, 9);
    const auto moves_pos = text.find(" moves ");
    if (moves_pos != std::string::npos)
        text = text.substr(0, moves_pos);
    return trim_copy(std::move(text));
}

std::string decode_position_string_to_sfen_with_ply(const std::string& position_command)
{
    const auto parsed = bookminer::parse_position_command(position_command);
    auto board = bookminer::SfenPosition::from_sfen(parsed.start_sfen_with_ply);
    for (const auto& move : parsed.moves)
        board.push_usi(move);
    return board.sfen_with_ply();
}

std::vector<std::pair<std::string, std::string>> position_prefixes(const std::string& position_command)
{
    std::vector<std::pair<std::string, std::string>> prefixes;
    const auto parsed = bookminer::parse_position_command(position_command);
    auto board = bookminer::SfenPosition::from_sfen(parsed.start_sfen_with_ply);

    std::string current_command = base_position_command(position_command);
    prefixes.emplace_back(current_command, board.sfen_with_ply());
    for (const auto& move : parsed.moves)
    {
        board.push_usi(move);
        current_command = append_position_move(current_command, move);
        prefixes.emplace_back(current_command, board.sfen_with_ply());
    }

    return prefixes;
}

struct PetaPositionHit {
    const bookminer::PositionInfo* position = nullptr;
    bool flipped = false;
};

PetaPositionHit find_peta_position_with_flip(const bookminer::BookStore& peta_book, const std::string& sfen)
{
    const auto key = bookminer::PackedSfen::from_sfen(sfen);
    const auto* position = peta_book.find_position(key);
    if (position && !position->moves.empty())
        return {position, false};

    const auto* flipped = peta_book.find_position(key.flipped());
    if (flipped && !flipped->moves.empty())
        return {flipped, true};

    if (position) return {position, false};
    if (flipped) return {flipped, true};
    return {};
}

bool visited_peta_position(const std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash>& visited, const std::string& sfen)
{
    const auto key = bookminer::PackedSfen::from_sfen(sfen);
    if (visited.find(key) != visited.end())
        return true;
    return visited.find(key.flipped()) != visited.end();
}

void append_unique_position_command(std::vector<std::string>& out, std::unordered_set<std::string>& seen, const std::string& position_command)
{
    if (seen.insert(position_command).second)
        out.push_back(position_command);
}

bool append_unique_peta_leaf(std::vector<std::string>& out,
                             std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash>& seen,
                             const std::string& position_command, const std::string& sfen)
{
    if (!seen.insert(bookminer::PackedSfen::from_sfen(sfen)).second)
        return false;
    out.push_back(position_command);
    return true;
}

struct PetaNextNode {
    std::string sfen_with_ply;
    int root_best_eval = 0;
    int eval_diff = 0;
    std::optional<int> leaf_eval;
};

struct PetaVisitKey {
    bookminer::PackedSfen position;
    int root_best_eval;
    int eval_diff;
    std::optional<int> leaf_eval;

    bool operator==(const PetaVisitKey& other) const noexcept
    {
        return position == other.position && root_best_eval == other.root_best_eval && eval_diff == other.eval_diff
            && leaf_eval == other.leaf_eval;
    }
};

struct PetaVisitKeyHash {
    std::size_t operator()(const PetaVisitKey& key) const noexcept
    {
        std::uint64_t hash = bookminer::PackedSfenHash{}(key.position);
        hash ^= static_cast<std::uint64_t>(static_cast<std::uint32_t>(key.root_best_eval)) * UINT64_C(0x9e3779b97f4a7c15);
        hash ^= static_cast<std::uint64_t>(static_cast<std::uint32_t>(key.eval_diff)) * UINT64_C(0xd6e8feb86659fd93);
        hash ^= key.leaf_eval ? static_cast<std::uint64_t>(static_cast<std::uint32_t>(*key.leaf_eval)) * UINT64_C(0xbf58476d1ce4e5b9)
                              : UINT64_C(0x94d049bb133111eb);
        hash ^= hash >> 30;
        hash *= UINT64_C(0xbf58476d1ce4e5b9);
        hash ^= hash >> 27;
        hash *= UINT64_C(0x94d049bb133111eb);
        hash ^= hash >> 31;
        return static_cast<std::size_t>(hash);
    }
};

bool mark_peta_state(std::unordered_map<PetaVisitKey, int, PetaVisitKeyHash>& visited,
                     const std::string& sfen, int ply, const PetaNextNode& node,
                     bool use_leaf_eval)
{
    PetaVisitKey key{bookminer::PackedSfen::from_sfen(sfen), node.root_best_eval, node.eval_diff,
                     use_leaf_eval ? node.leaf_eval : std::nullopt};
    auto [it, inserted] = visited.emplace(std::move(key), ply);
    if (!inserted && it->second <= ply)
        return false;
    it->second = ply;
    return true;
}

bool peta_is_own_turn(const std::string& sfen, int turn)
{
    const auto separator = sfen.find(' ');
    if (separator == std::string::npos || separator + 1 >= sfen.size()
        || (sfen[separator + 1] != 'b' && sfen[separator + 1] != 'w'))
        throw std::runtime_error("invalid SFEN side to move: " + sfen);
    return (sfen[separator + 1] == 'b') == (turn == 1);
}

class OrderedPetaNextPositions {
public:
    bool empty() const noexcept { return order_.empty(); }
    const std::vector<std::string>& order() const noexcept { return order_; }
    const PetaNextNode& at(const std::string& position_command) const { return values_.at(position_command); }

    void set(const std::string& position_command, PetaNextNode node)
    {
        if (values_.find(position_command) == values_.end())
            order_.push_back(position_command);
        values_[position_command] = std::move(node);
    }

    void set_root(const std::string& position_command, PetaNextNode node)
    {
        const auto existing = values_.find(position_command);
        if (existing != values_.end())
            node.eval_diff = std::max(node.eval_diff, existing->second.eval_diff);
        set(position_command, std::move(node));
    }

private:
    std::vector<std::string> order_;
    std::unordered_map<std::string, PetaNextNode> values_;
};

std::string path_to_utf8(const fs::path& path)
{
    const auto text = path.u8string();
    return std::string(text.begin(), text.end());
}

struct PetaRootPosition {
    std::string position_command;
    std::string sfen_with_ply;
    std::optional<int> eval_diff;
};

struct PetaNextCancelled {};

void check_peta_next_cancel(const std::atomic<bool>* cancelled)
{
    if (cancelled && cancelled->load())
        throw PetaNextCancelled{};
}

std::vector<PetaRootPosition> load_peta_next_root_positions(const std::filesystem::path& start_sfens_path, const std::atomic<bool>* cancelled = nullptr)
{
    std::vector<PetaRootPosition> roots;
    const bool has_start_sfens_file = fs::exists(start_sfens_path);

    if (has_start_sfens_file)
    {
        if (!fs::is_regular_file(start_sfens_path))
            throw std::runtime_error("peta_next start sfens path is not a file: " + path_to_utf8(start_sfens_path));
        log_line("read start sfens , path = " + path_to_utf8(start_sfens_path));
        std::ifstream in(start_sfens_path);
        if (!in)
            throw std::runtime_error("failed to open peta_next start sfens file: " + path_to_utf8(start_sfens_path));

        std::string line;
        std::size_t line_number = 0;
        while (std::getline(in, line))
        {
            check_peta_next_cancel(cancelled);
            ++line_number;
            if (!line.empty() && line.back() == '\r')
                line.pop_back();
            if (roots.empty() && line.size() >= 3
                && static_cast<unsigned char>(line[0]) == 0xef
                && static_cast<unsigned char>(line[1]) == 0xbb
                && static_cast<unsigned char>(line[2]) == 0xbf)
                line.erase(0, 3);
            const auto comment = line.find("//");
            if (comment != std::string::npos)
                line.erase(comment);
            auto first = std::find_if(line.begin(), line.end(), [](unsigned char ch) {
                return !std::isspace(ch);
            });
            if (first == line.end())
                continue;
            if (*first == '#')
                continue;

            std::string position_command = trim_metadata_copy(std::string(first, line.end()));
            std::optional<int> eval_diff;
            const auto comma = position_command.find(',');
            if (comma != std::string::npos)
            {
                const auto raw_diff = trim_metadata_copy(position_command.substr(comma + 1));
                position_command = trim_metadata_copy(position_command.substr(0, comma));
                try {
                    if (position_command.empty() || raw_diff.empty()
                        || !std::all_of(raw_diff.begin(), raw_diff.end(), [](unsigned char c) { return c >= '0' && c <= '9'; }))
                        throw std::runtime_error("invalid eval_diff");
                    eval_diff = parse_integer(raw_diff);
                } catch (const std::exception&) {
                    throw std::runtime_error(path_to_utf8(start_sfens_path) + ":" + std::to_string(line_number)
                                             + ": eval_diff must be a non-negative integer");
                }
            }
            const std::string sfen_with_ply = decode_position_string_to_sfen_with_ply(position_command);
            log_line("start sfen = " + sfen_with_ply + (eval_diff ? " , eval_diff = " + std::to_string(*eval_diff) : ""));
            roots.push_back({position_command, sfen_with_ply, eval_diff});
        }
        if (in.bad())
            throw std::runtime_error("failed to read peta_next start sfens file: " + path_to_utf8(start_sfens_path));
    }

    if (!has_start_sfens_file)
        roots.push_back({"startpos", bookminer::StartSfenPly1, std::nullopt});

    return roots;
}

struct PetaNextLookup {
    const bookminer::PositionInfo* peta = nullptr;
    std::optional<bookminer::PositionInfo> normal;
    bool flipped = false;
    const bookminer::PositionInfo* position() const { return normal ? &*normal : peta; }
};

PetaNextLookup find_peta_next_position(const bookminer::BookStore& peta_book,
                                       const bookminer::BookStore* fallback_book,
                                       const std::string& sfen)
{
    const auto hit = find_peta_position_with_flip(peta_book, sfen);
    if (hit.position != nullptr && !hit.position->moves.empty())
        return {hit.position, std::nullopt, hit.flipped};
    if (fallback_book != nullptr)
    {
        auto normal = find_considered_book_position_with_flip(*fallback_book, sfen);
        if (has_considered(normal.position))
            return {nullptr, std::move(normal.position), normal.flipped};
    }
    return {hit.position, std::nullopt, hit.flipped};
}

// Only an unextended best line is cut by eval_limit. Normal-book depths
// are not propagated, so test the best move's child in the normal book.
bool peta_cut_by_eval_limit(const bookminer::PositionInfo& position, bool flipped,
                            const std::string& sfen_with_ply, std::optional<int> limit,
                            const bookminer::BookStore* normal_book = nullptr,
                            bool normal_position = false)
{
    if (!limit || position.moves.empty()) return false;
    const auto best = std::max_element(position.moves.begin(), position.moves.end(),
        [](const auto& a, const auto& b) { return a.eval < b.eval; });
    if (std::abs(static_cast<int>(best->eval)) <= *limit) return false;
    if (!normal_position && best->depth != 0) return false;
    if (normal_book)
    {
        const auto move16 = flipped ? bookminer::flipped_move16(best->move16) : best->move16;
        const auto move = bookminer::move16_to_usi(move16);
        if (!move.empty())
        {
            auto board = bookminer::SfenPosition::from_sfen(sfen_with_ply);
            board.push_usi(move);
            if (has_considered(find_considered_book_position_with_flip(*normal_book, board.sfen()).position))
                return false;
        }
    }
    return true;
}

std::vector<std::string> peta_next_for_turn(
    const bookminer::BookStore* source_book,
    const bookminer::BookStore& peta_book,
    int turn,
    int peta_eval_diff,
    int max_step,
    int max_book_ply,
    const std::filesystem::path& start_sfens_path,
    std::optional<int> eval_refutation_margin,
    const bookminer::BookStore* fallback_book = nullptr,
    const std::vector<PetaRootPosition>* loaded_roots = nullptr,
    const std::atomic<bool>* cancelled = nullptr,
    std::optional<int> eval_limit = std::nullopt)
{
    std::vector<std::string> think_sfens;
    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> think_seen;
    std::size_t skipped_ply = 0, skipped_visited = 0, rejected_own = 0, rejected_opponent = 0;
    std::size_t missing_roots = 0, duplicate_leaves = 0, limited_frontier = 0, fallback_hits = 0;
    std::size_t fallback_nonbest_moves = 0;
    auto record_leaf = [&](const std::string& command, const std::string& sfen) {
        if (!append_unique_peta_leaf(think_sfens, think_seen, command, sfen))
            ++duplicate_leaves;
    };

    const std::string turn_str = turn == 1 ? "black" : "white";
    const bool filter_refutation = source_book != nullptr && eval_refutation_margin.has_value();
    log_line(std::string("--- ") + (filter_refutation ? "peta_next_refutation " : "peta_next ") + turn_str + " ---");

    std::unordered_map<PetaVisitKey, int, PetaVisitKeyHash> visited;
    const auto local_roots = loaded_roots ? std::vector<PetaRootPosition>{} : load_peta_next_root_positions(start_sfens_path, cancelled);
    const auto& root_positions = loaded_roots ? *loaded_roots : local_roots;

    OrderedPetaNextPositions current_positions;
    for (const auto& [position_command, sfen_with_ply, root_eval_diff] : root_positions)
    {
        check_peta_next_cancel(cancelled);
        auto [sfen, ply] = bookminer::trim_sfen_ply(sfen_with_ply);
        if (ply >= max_book_ply)
        {
            ++skipped_ply;
            continue;
        }

        const auto hit = find_peta_next_position(peta_book, fallback_book, sfen);
        const auto* position = hit.position();
        if (position == nullptr || position->moves.empty())
        {
            ++missing_roots;
            if (!filter_refutation)
                record_leaf(position_command, sfen);
            continue;
        }

        const auto root_best = get_best(*position);
        if (!root_best.has_value())
        {
            if (!filter_refutation)
                record_leaf(position_command, sfen);
            continue;
        }

        const int effective_diff = root_eval_diff.value_or(peta_eval_diff);
        current_positions.set_root(position_command, PetaNextNode{sfen_with_ply, root_best->first, effective_diff, std::nullopt});
        log_line("root sfen : " + sfen_with_ply + " , root_best = " + std::to_string(root_best->first)
                 + " , eval_diff = " + std::to_string(effective_diff));
    }

    int step = 1;
    while (!current_positions.empty())
    {
        if (step > max_step)
        {
            limited_frontier = current_positions.order().size();
            break;
        }

        OrderedPetaNextPositions next_positions;

        for (const auto& position_command : current_positions.order())
        {
            check_peta_next_cancel(cancelled);
            const auto& node = current_positions.at(position_command);
            auto [sfen, ply] = bookminer::trim_sfen_ply(node.sfen_with_ply);

            if (ply >= max_book_ply)
            {
                ++skipped_ply;
                continue;
            }

            if (!mark_peta_state(visited, sfen, ply, node, false))
            {
                ++skipped_visited;
                continue;
            }

            const auto hit = find_peta_next_position(peta_book, fallback_book, sfen);
            const auto* position = hit.position();
            if (position == nullptr || (fallback_book != nullptr && position->moves.empty()))
            {
                if (!filter_refutation)
                    record_leaf(position_command, sfen);
                continue;
            }

            if (hit.normal)
                ++fallback_hits;
            const auto& moveinfos = position->moves;
            if (moveinfos.empty())
                continue;

            if (peta_cut_by_eval_limit(*position, hit.flipped, node.sfen_with_ply, eval_limit, fallback_book, hit.normal.has_value()))
                continue;

            const int best_eval = hit.normal ? get_best(*position)->first : static_cast<int>(moveinfos.front().eval);
            const bool own_turn = peta_is_own_turn(sfen, turn);
            const std::int64_t eval_low = own_turn
                ? best_eval : static_cast<std::int64_t>(node.root_best_eval) - node.eval_diff;

            for (std::size_t move_index = 0; move_index < moveinfos.size(); ++move_index)
            {
                const auto& moveinfo = moveinfos[move_index];
                if (static_cast<int>(moveinfo.eval) < eval_low)
                {
                    ++(own_turn ? rejected_own : rejected_opponent);
                    continue;
                }

                const std::uint16_t move16 = hit.flipped ? bookminer::flipped_move16(moveinfo.move16) : moveinfo.move16;
                const std::string move = bookminer::move16_to_usi(move16);
                if (move.empty())
                    continue;

                auto board = bookminer::SfenPosition::from_sfen(node.sfen_with_ply);
                board.push_usi(move);

                const std::string next_sfen_with_ply = board.sfen_with_ply();
                const auto next_sfen_ply = bookminer::trim_sfen_ply(next_sfen_with_ply);
                if (next_sfen_ply.second >= max_book_ply)
                {
                    ++skipped_ply;
                    continue;
                }

                const std::string next_position_command = append_position_move(position_command, move);
                if (hit.normal && static_cast<int>(moveinfo.eval) < best_eval)
                    ++fallback_nonbest_moves;

                if (filter_refutation)
                {
                    const auto next_hit = find_peta_position_with_flip(peta_book, next_sfen_ply.first);
                    if (next_hit.position == nullptr)
                    {
                        if (move_index == 0
                            && moveinfo.depth == 0
                            && is_refuted_move_by_peta_impact(
                                *source_book,
                                *position,
                                hit.flipped,
                                sfen,
                                move16,
                                static_cast<int>(moveinfo.eval),
                                *eval_refutation_margin))
                        {
                            record_leaf(next_position_command, next_sfen_ply.first);
                        }
                        continue;
                    }
                }

                next_positions.set(next_position_command, PetaNextNode{next_sfen_with_ply, -node.root_best_eval, node.eval_diff, std::nullopt});
            }
        }

        log_line("step = " + std::to_string(step)
            + " , len(next_positions) = " + std::to_string(next_positions.order().size())
            + ", think_sfens = " + std::to_string(think_sfens.size())
            + ", fallback_nonbest = " + std::to_string(fallback_nonbest_moves));

        current_positions = std::move(next_positions);
        ++step;
    }

    log_line("peta_next stats " + turn_str
        + " roots=" + std::to_string(root_positions.size())
        + " missing_roots=" + std::to_string(missing_roots)
        + " skipped_ply=" + std::to_string(skipped_ply)
        + " skipped_visited=" + std::to_string(skipped_visited)
        + " rejected_own=" + std::to_string(rejected_own)
        + " rejected_opponent=" + std::to_string(rejected_opponent)
        + " fallback_hits=" + std::to_string(fallback_hits)
        + " fallback_nonbest=" + std::to_string(fallback_nonbest_moves)
        + " duplicate_leaves=" + std::to_string(duplicate_leaves)
        + " max_step_frontier=" + std::to_string(limited_frontier)
        + " leaves=" + std::to_string(think_sfens.size()));
    return think_sfens;
}

template <typename Writer>
void write_position_file(const std::filesystem::path& path, Writer write)
{
    if (path.has_parent_path())
        fs::create_directories(path.parent_path());

    const auto temporary = bookminer::temp_book_path(path);
    try
    {
        std::ofstream out(temporary);
        if (!out)
            throw std::runtime_error("failed to open output file: " + temporary.string());
        write(out);
        out.close();
        if (!out)
            throw std::runtime_error("failed to write output file: " + temporary.string());
        bookminer::replace_book_file(temporary, path);
    }
    catch (...)
    {
        std::error_code ec;
        fs::remove(temporary, ec);
        throw;
    }
}

std::size_t write_position_commands_file(const std::filesystem::path& path, const std::vector<std::string>& position_commands)
{
    write_position_file(path, [&](std::ostream& out) {
        for (const auto& position_command : position_commands)
            out << position_command << '\n';
    });

    return position_commands.size();
}

std::size_t write_position_command_entries_file(const std::filesystem::path& path, const std::vector<PositionCommandEntry>& position_commands, const std::atomic<bool>* cancelled = nullptr)
{
    write_position_file(path, [&](std::ostream& out) {
        for (const auto& position_command : position_commands)
        {
            check_peta_next_cancel(cancelled);
            out << format_position_command_entry(position_command) << '\n';
        }
        check_peta_next_cancel(cancelled);
    });

    return position_commands.size();
}

std::vector<PositionCommandEntry> make_position_command_entries(
    const std::vector<std::string>& position_commands,
    std::optional<int> book_extend_ply,
    std::optional<int> eval_limit,
    std::optional<int> max_book_ply,
    const std::atomic<bool>* cancelled = nullptr)
{
    std::vector<PositionCommandEntry> entries;
    entries.reserve(position_commands.size());
    for (const auto& position_command : position_commands)
    {
        check_peta_next_cancel(cancelled);
        entries.push_back(PositionCommandEntry{position_command, book_extend_ply, eval_limit, max_book_ply});
    }
    return entries;
}

std::size_t merge_black_white_think_sfens(const std::filesystem::path& black_path, const std::filesystem::path& white_path, const std::filesystem::path& output_path, const std::atomic<bool>* cancelled = nullptr)
{
    std::ifstream black(black_path);
    std::ifstream white(white_path);
    if (!black)
        throw std::runtime_error("failed to open file: " + black_path.string());
    if (!white)
        throw std::runtime_error("failed to open file: " + white_path.string());
    std::size_t count = 0;
    std::unordered_set<std::string> seen;
    write_position_file(output_path, [&](std::ostream& out) {
        while (true)
        {
            check_peta_next_cancel(cancelled);
            std::string black_line;
            std::string white_line;
            const bool has_black = static_cast<bool>(std::getline(black, black_line));
            const bool has_white = static_cast<bool>(std::getline(white, white_line));
            if (!has_black && !has_white)
                break;

            if (has_black && seen.insert(black_line).second)
            {
                out << black_line << '\n';
                ++count;
            }
            if (has_white && seen.insert(white_line).second)
            {
                out << white_line << '\n';
                ++count;
            }
        }
        if (black.bad() || white.bad())
            throw std::runtime_error("failed to read think_sfens merge input");
        check_peta_next_cancel(cancelled);
    });

    return count;
}

std::string peta_next(
    const bookminer::BookStore* source_book,
    const bookminer::BookStore& peta_book,
    int peta_eval_diff,
    int max_step,
    int max_book_ply,
    const std::filesystem::path& start_sfens_path,
    std::optional<int> eval_refutation_margin,
    std::optional<int> book_extend_ply,
    std::optional<int> eval_limit,
    const bookminer::BookStore* fallback_book = nullptr,
    const std::atomic<bool>* cancelled = nullptr)
{
    const bool filter_refutation = source_book != nullptr && eval_refutation_margin.has_value();
    log_line(
        std::string(filter_refutation ? "peta_next_refutation" : "peta_next")
        + ", default_eval_diff = " + std::to_string(peta_eval_diff)
        + ", max_step = " + std::to_string(max_step)
        + ", max_book_ply = " + std::to_string(max_book_ply)
        + ", book_extend_ply = " + (book_extend_ply.has_value() ? std::to_string(*book_extend_ply) : std::string("None"))
        + ", eval_limit = " + (eval_limit.has_value() ? std::to_string(*eval_limit) : std::string("None"))
        + ", start_sfens_path = " + path_to_utf8(start_sfens_path)
        + ", normal_book_fallback = " + std::string(fallback_book ? "on" : "off")
        + ", peta_book_positions = " + std::to_string(peta_book.size())
        + (filter_refutation ? ", eval_refutation_margin = " + std::to_string(*eval_refutation_margin) : ""));

    const auto roots = load_peta_next_root_positions(start_sfens_path, cancelled);
    check_peta_next_cancel(cancelled);
    const auto black = peta_next_for_turn(source_book, peta_book, 1, peta_eval_diff, max_step, max_book_ply, start_sfens_path, eval_refutation_margin, fallback_book, &roots, cancelled, eval_limit);
    check_peta_next_cancel(cancelled);
    const fs::path black_path = fs::path(BookDir) / "think_sfens-black.txt";
    log_line("write book path = " + black_path.string() + ", len(think_sfens) = " + std::to_string(black.size()) + ".");
    write_position_command_entries_file(
        black_path,
        make_position_command_entries(black, book_extend_ply, eval_limit, max_book_ply, cancelled), cancelled);

    const auto white = peta_next_for_turn(source_book, peta_book, 0, peta_eval_diff, max_step, max_book_ply, start_sfens_path, eval_refutation_margin, fallback_book, &roots, cancelled, eval_limit);
    check_peta_next_cancel(cancelled);
    const fs::path white_path = fs::path(BookDir) / "think_sfens-white.txt";
    log_line("write book path = " + white_path.string() + ", len(think_sfens) = " + std::to_string(white.size()) + ".");
    write_position_command_entries_file(
        white_path,
        make_position_command_entries(white, book_extend_ply, eval_limit, max_book_ply, cancelled), cancelled);

    const fs::path output_path = fs::path(BookDir) / ThinkSfensName;
    check_peta_next_cancel(cancelled);
    const std::size_t count = merge_black_white_think_sfens(black_path, white_path, output_path, cancelled);

    log_line(std::string(filter_refutation ? "peta_next_refutation" : "peta_next") + " done.");
    return std::string("[") + (filter_refutation ? "PetaNextRefutationDone" : "PetaNextDone")
        + "] path=" + output_path.string() + " count=" + std::to_string(count);
}

bool peta_leaf_rejoins_peta_book(
    const bookminer::BookStore& peta_book,
    const std::string& sfen_with_ply,
    std::optional<int> leaf_eval = std::nullopt,
    std::optional<int> eval_drop = std::nullopt)
{
    const bool filter_by_eval_drop = eval_drop.has_value() && *eval_drop > PetaRejoinEvalDropDisabled;
    const auto board = bookminer::SfenPosition::from_sfen(sfen_with_ply);
    for (const auto& move : board.legal_moves())
    {
        auto next_board = board;
        next_board.push_usi(move);
        const auto [next_sfen, _next_ply] = bookminer::trim_sfen_ply(next_board.sfen_with_ply());
        const auto hit = find_peta_position_with_flip(peta_book, next_sfen);
        if (hit.position == nullptr)
            continue;

        if (!filter_by_eval_drop)
            return true;

        if (!leaf_eval.has_value())
            continue;

        const auto best = best_moveinfo_in_sfen_orientation(*hit.position, hit.flipped);
        if (best.info == nullptr)
            continue;

        if (*leaf_eval - static_cast<int>(best.info->eval) >= *eval_drop)
            return true;
    }
    return false;
}

struct PetaRejoinStats {
    std::size_t processed_nodes = 0;
    std::size_t leaf_nodes = 0;
    std::size_t rejoin_nodes = 0;
};

std::vector<std::string> peta_rejoin_for_turn(
    const bookminer::BookStore& peta_book,
    int turn,
    int peta_eval_diff,
    int max_step,
    int max_book_ply,
    const std::filesystem::path& start_sfens_path,
    std::optional<int> eval_drop,
    PetaRejoinStats& stats,
    const std::vector<PetaRootPosition>* loaded_roots = nullptr,
    std::optional<int> eval_limit = std::nullopt)
{
    std::vector<std::string> think_sfens;
    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> think_seen;

    const std::string turn_str = turn == 1 ? "black" : "white";
    log_line("--- peta_rejoin " + turn_str + " ---");

    std::unordered_map<PetaVisitKey, int, PetaVisitKeyHash> visited;
    const auto local_roots = loaded_roots ? std::vector<PetaRootPosition>{} : load_peta_next_root_positions(start_sfens_path);
    const auto& root_positions = loaded_roots ? *loaded_roots : local_roots;

    OrderedPetaNextPositions current_positions;
    for (const auto& [position_command, sfen_with_ply, root_eval_diff] : root_positions)
    {
        auto [sfen, ply] = bookminer::trim_sfen_ply(sfen_with_ply);
        if (ply >= max_book_ply)
            continue;

        const auto hit = find_peta_position_with_flip(peta_book, sfen);
        if (hit.position == nullptr || hit.position->moves.empty())
        {
            ++stats.leaf_nodes;
            if (peta_leaf_rejoins_peta_book(peta_book, sfen_with_ply, std::nullopt, eval_drop))
            {
                if (append_unique_peta_leaf(think_sfens, think_seen, position_command, sfen))
                    ++stats.rejoin_nodes;
            }
            continue;
        }

        const auto root_best = get_best(*hit.position);
        if (!root_best.has_value())
        {
            ++stats.leaf_nodes;
            if (peta_leaf_rejoins_peta_book(peta_book, sfen_with_ply, std::nullopt, eval_drop))
            {
                if (append_unique_peta_leaf(think_sfens, think_seen, position_command, sfen))
                    ++stats.rejoin_nodes;
            }
            continue;
        }

        const int effective_diff = root_eval_diff.value_or(peta_eval_diff);
        current_positions.set_root(position_command, PetaNextNode{sfen_with_ply, root_best->first, effective_diff, std::nullopt});
        log_line("root sfen : " + sfen_with_ply + " , root_best = " + std::to_string(root_best->first)
                 + " , eval_diff = " + std::to_string(effective_diff));
    }

    int step = 1;
    while (!current_positions.empty())
    {
        if (step > max_step)
            break;

        OrderedPetaNextPositions next_positions;

        for (const auto& position_command : current_positions.order())
        {
            ++stats.processed_nodes;

            const auto& node = current_positions.at(position_command);
            auto [sfen, ply] = bookminer::trim_sfen_ply(node.sfen_with_ply);

            if (ply >= max_book_ply)
                continue;

            const auto hit = find_peta_position_with_flip(peta_book, sfen);
            const bool leaf = hit.position == nullptr || hit.position->moves.empty();
            const bool needs_leaf_eval = leaf && eval_drop.has_value()
                && *eval_drop > PetaRejoinEvalDropDisabled;
            if (!mark_peta_state(visited, sfen, ply, node, needs_leaf_eval))
                continue;

            if (leaf)
            {
                ++stats.leaf_nodes;
                if (peta_leaf_rejoins_peta_book(peta_book, node.sfen_with_ply, node.leaf_eval, eval_drop))
                {
                    if (append_unique_peta_leaf(think_sfens, think_seen, position_command, sfen))
                        ++stats.rejoin_nodes;
                }
                continue;
            }

            const auto& moveinfos = hit.position->moves;
            if (peta_cut_by_eval_limit(*hit.position, hit.flipped, node.sfen_with_ply, eval_limit))
                continue;
            const int best_eval = static_cast<int>(moveinfos.front().eval);
            const std::int64_t eval_low = peta_is_own_turn(sfen, turn)
                ? best_eval : static_cast<std::int64_t>(node.root_best_eval) - node.eval_diff;

            for (const auto& moveinfo : moveinfos)
            {
                if (static_cast<int>(moveinfo.eval) < eval_low)
                    continue;

                const std::uint16_t move16 = hit.flipped ? bookminer::flipped_move16(moveinfo.move16) : moveinfo.move16;
                const std::string move = bookminer::move16_to_usi(move16);
                if (move.empty())
                    continue;

                auto board = bookminer::SfenPosition::from_sfen(node.sfen_with_ply);
                board.push_usi(move);

                const std::string next_sfen_with_ply = board.sfen_with_ply();
                const auto next_sfen_ply = bookminer::trim_sfen_ply(next_sfen_with_ply);
                if (next_sfen_ply.second >= max_book_ply)
                    continue;

                const std::string next_position_command = append_position_move(position_command, move);
                next_positions.set(
                    next_position_command,
                    PetaNextNode{next_sfen_with_ply, -node.root_best_eval, node.eval_diff, static_cast<int>(moveinfo.eval)});
            }
        }

        log_line("step = " + std::to_string(step)
            + " , len(next_positions) = " + std::to_string(next_positions.order().size())
            + ", think_sfens = " + std::to_string(think_sfens.size()));

        current_positions = std::move(next_positions);
        ++step;
    }

    return think_sfens;
}

std::string peta_rejoin(
    const bookminer::BookStore& peta_book,
    int peta_eval_diff,
    int max_step,
    int max_book_ply,
    const std::filesystem::path& start_sfens_path,
    std::optional<int> book_extend_ply,
    std::optional<int> eval_limit,
    std::optional<int> eval_drop = std::nullopt)
{
    log_line(
        "peta_rejoin, default_eval_diff = " + std::to_string(peta_eval_diff)
        + ", max_step = " + std::to_string(max_step)
        + ", max_book_ply = " + std::to_string(max_book_ply)
        + ", eval_drop = " + (eval_drop.has_value() ? std::to_string(*eval_drop) : std::string("None"))
        + ", book_extend_ply = " + (book_extend_ply.has_value() ? std::to_string(*book_extend_ply) : std::string("None"))
        + ", eval_limit = " + (eval_limit.has_value() ? std::to_string(*eval_limit) : std::string("None"))
        + ", start_sfens_path = " + path_to_utf8(start_sfens_path));

    PetaRejoinStats stats;
    const auto roots = load_peta_next_root_positions(start_sfens_path);
    const auto black = peta_rejoin_for_turn(peta_book, 1, peta_eval_diff, max_step, max_book_ply, start_sfens_path, eval_drop, stats, &roots, eval_limit);
    const fs::path black_path = fs::path(BookDir) / "think_sfens-black.txt";
    log_line("write book path = " + black_path.string() + ", len(think_sfens) = " + std::to_string(black.size()) + ".");
    write_position_command_entries_file(
        black_path,
        make_position_command_entries(black, book_extend_ply, eval_limit, max_book_ply));

    const auto white = peta_rejoin_for_turn(peta_book, 0, peta_eval_diff, max_step, max_book_ply, start_sfens_path, eval_drop, stats, &roots, eval_limit);
    const fs::path white_path = fs::path(BookDir) / "think_sfens-white.txt";
    log_line("write book path = " + white_path.string() + ", len(think_sfens) = " + std::to_string(white.size()) + ".");
    write_position_command_entries_file(
        white_path,
        make_position_command_entries(white, book_extend_ply, eval_limit, max_book_ply));

    const fs::path output_path = fs::path(BookDir) / ThinkSfensName;
    const std::size_t count = merge_black_white_think_sfens(black_path, white_path, output_path);

    log_line("peta_rejoin done.");
    return "[PetaRejoinDone] path=" + output_path.string()
        + " count=" + std::to_string(count)
        + " processed_nodes=" + std::to_string(stats.processed_nodes)
        + " leaf_nodes=" + std::to_string(stats.leaf_nodes)
        + " rejoin_nodes=" + std::to_string(stats.rejoin_nodes);
}

std::optional<std::string> peta_pv_leaf_position_command(
    const bookminer::BookStore& peta_book,
    std::string position_command,
    const std::string& sfen_with_ply,
    std::uint16_t first_move16,
    int max_book_ply,
    int max_step = PetaDefaultMaxStep,
    std::optional<int> eval_limit = std::nullopt)
{
    if (max_step <= 0)
        return std::nullopt;

    const std::string first_move = bookminer::move16_to_usi(first_move16);
    if (first_move.empty())
        return std::nullopt;

    auto board = bookminer::SfenPosition::from_sfen(sfen_with_ply);
    board.push_usi(first_move);

    std::string next_sfen_with_ply = board.sfen_with_ply();
    if (bookminer::trim_sfen_ply(next_sfen_with_ply).second >= max_book_ply)
        return std::nullopt;

    std::string leaf_position_command = append_position_move(std::move(position_command), first_move);
    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> visited;
    int step = 1;

    while (true)
    {
        const auto [current_sfen, current_ply] = bookminer::trim_sfen_ply(next_sfen_with_ply);
        if (current_ply >= max_book_ply)
            return std::nullopt;
        if (visited_peta_position(visited, current_sfen))
            return std::nullopt;
        visited.insert(bookminer::PackedSfen::from_sfen(current_sfen));

        const auto hit = find_peta_position_with_flip(peta_book, current_sfen);
        if (hit.position == nullptr || hit.position->moves.empty())
            return leaf_position_command;

        const auto& best = hit.position->moves.front();
        if (best.depth <= 0)
        {
            if (eval_limit && std::abs(static_cast<int>(best.eval)) > *eval_limit)
                return std::nullopt;
            return leaf_position_command;
        }
        if (step >= max_step)
            return std::nullopt;

        const std::uint16_t move16 = hit.flipped ? bookminer::flipped_move16(best.move16) : best.move16;
        const std::string move = bookminer::move16_to_usi(move16);
        if (move.empty())
            return std::nullopt;

        auto next_board = bookminer::SfenPosition::from_sfen(next_sfen_with_ply);
        next_board.push_usi(move);
        next_sfen_with_ply = next_board.sfen_with_ply();
        if (bookminer::trim_sfen_ply(next_sfen_with_ply).second >= max_book_ply)
            return std::nullopt;

        leaf_position_command = append_position_move(std::move(leaf_position_command), move);
        ++step;
    }
}

std::string peta_unsolved(
    const bookminer::BookStore& peta_book,
    const std::vector<int>& min_depths,
    int max_book_ply,
    int max_step,
    std::optional<int> book_extend_ply,
    std::optional<int> eval_limit)
{
    const fs::path input_path = fs::path(BookDir) / ThinkUnsolvedSfensName;
    log_line("peta_unsolved, min_depth_count = " + std::to_string(min_depths.size())
        + ", max_book_ply = " + std::to_string(max_book_ply)
        + ", max_step = " + std::to_string(max_step)
        + ", book_extend_ply = " + (book_extend_ply.has_value() ? std::to_string(*book_extend_ply) : std::string("None"))
        + ", eval_limit = " + (eval_limit.has_value() ? std::to_string(*eval_limit) : std::string("None"))
        + ", unsolved_sfens_path = " + input_path.string());

    std::ifstream input(input_path);
    if (!input)
        throw std::runtime_error("think_unsolved_sfens file not found : " + input_path.string());

    std::vector<std::string> think_sfens;
    std::unordered_set<std::string> think_seen;
    std::size_t input_games = 0;
    std::size_t processed_prefixes = 0;
    std::size_t candidates = 0;
    std::size_t stopped_by_eval_limit = 0;
    std::size_t skipped_by_ply = 0;
    std::size_t skipped_no_peta = 0;
    std::size_t skipped_no_leaf = 0;

    std::string raw_line;
    bool first_line = true;
    while (std::getline(input, raw_line))
    {
        if (first_line && raw_line.size() >= 3
            && static_cast<unsigned char>(raw_line[0]) == 0xef
            && static_cast<unsigned char>(raw_line[1]) == 0xbb
            && static_cast<unsigned char>(raw_line[2]) == 0xbf)
            raw_line.erase(0, 3);
        first_line = false;
        if (!raw_line.empty() && raw_line.back() == '\r')
            raw_line.pop_back();
        const std::string position_line = trim_copy(raw_line);
        if (position_line.empty() || position_line.front() == '#')
            continue;

        ++input_games;
        const auto prefixes = position_prefixes(position_line);
        if (prefixes.empty())
            continue;

        for (const auto& [prefix_command, prefix_sfen_with_ply] : prefixes)
        {
            ++processed_prefixes;
            if (processed_prefixes % PetaUnsolvedProgressInterval == 0)
            {
                log_line("unsolved progress prefixes = " + std::to_string(processed_prefixes)
                    + " , think_sfens = " + std::to_string(think_sfens.size())
                    + " , stopped_by_eval_limit = " + std::to_string(stopped_by_eval_limit)
                    + " , skipped_by_ply = " + std::to_string(skipped_by_ply)
                    + " , skipped_no_peta = " + std::to_string(skipped_no_peta));
            }

            const auto [prefix_sfen, prefix_ply] = bookminer::trim_sfen_ply(prefix_sfen_with_ply);
            if (prefix_ply >= max_book_ply)
            {
                ++skipped_by_ply;
                continue;
            }

            const auto hit = find_peta_position_with_flip(peta_book, prefix_sfen);
            if (hit.position == nullptr || hit.position->moves.empty())
            {
                ++skipped_no_peta;
                continue;
            }
            const auto& moves = hit.position->moves;
            const auto& best = moves.front();
            if (best.depth == 0 && eval_limit && std::abs(static_cast<int>(best.eval)) > *eval_limit)
            {
                ++stopped_by_eval_limit;
                break;
            }
            for (std::size_t i = 0; i < std::min(moves.size(), min_depths.size()); ++i)
            {
                if (static_cast<int>(moves[i].depth) >= min_depths[i])
                    continue;
                ++candidates;
                const auto move = hit.flipped ? bookminer::flipped_move16(moves[i].move16) : moves[i].move16;
                const auto leaf = peta_pv_leaf_position_command(
                    peta_book, prefix_command, prefix_sfen_with_ply, move, max_book_ply, max_step, eval_limit);
                if (!leaf)
                    ++skipped_no_leaf;
                else
                    append_unique_position_command(think_sfens, think_seen, *leaf);
            }
        }
    }

    const fs::path output_path = fs::path(BookDir) / ThinkSfensName;
    log_line("write book path = " + output_path.string() + ", len(think_sfens) = " + std::to_string(think_sfens.size()) + ".");
    write_position_command_entries_file(
        output_path,
        make_position_command_entries(think_sfens, book_extend_ply, eval_limit, max_book_ply));

    log_line("peta_unsolved done.");
    return "[PetaUnsolvedDone] path=" + output_path.string()
        + " count=" + std::to_string(think_sfens.size())
        + " input_games=" + std::to_string(input_games)
        + " processed_prefixes=" + std::to_string(processed_prefixes)
        + " candidates=" + std::to_string(candidates)
        + " stopped_by_eval_limit=" + std::to_string(stopped_by_eval_limit)
        + " skipped_by_ply=" + std::to_string(skipped_by_ply)
        + " skipped_no_peta=" + std::to_string(skipped_no_peta)
        + " skipped_no_leaf=" + std::to_string(skipped_no_leaf);
}

std::string peta_refutation(
    const bookminer::BookStore& book,
    const bookminer::BookStore& peta_book,
    int eval_refutation_margin,
    std::optional<int> eval_limit,
    int max_book_ply)
{
    log_line(
        "peta_refutation, eval_refutation_margin = " + std::to_string(eval_refutation_margin)
        + ", eval_limit = " + (eval_limit.has_value() ? std::to_string(*eval_limit) : std::string("none"))
        + ", max_book_ply = " + std::to_string(max_book_ply));

    std::vector<std::string> think_sfens;
    std::unordered_set<std::string> think_seen;
    std::size_t skipped_by_eval_limit = 0;
    std::size_t skipped_by_ply = 0;
    const auto peta_entries = peta_book.snapshot_entries();
    const std::size_t total = peta_entries.size();

    for (std::size_t index = 0; index < peta_entries.size(); ++index)
    {
        const std::size_t processed = index + 1;
        if (processed % PetaRefutationProgressInterval == 0)
        {
            log_line("refutation progress nodes = " + std::to_string(processed) + "/" + std::to_string(total)
                + " , think_sfens = " + std::to_string(think_sfens.size())
                + " , skipped_by_eval_limit = " + std::to_string(skipped_by_eval_limit)
                + " , skipped_by_ply = " + std::to_string(skipped_by_ply));
        }

        const auto& entry = peta_entries[index];
        const auto& peta_position = entry.position;
        if (peta_position.moves.empty())
            continue;

        if (static_cast<int>(peta_position.ply) + 1 >= max_book_ply)
        {
            ++skipped_by_ply;
            continue;
        }

        const auto& peta_best = peta_position.moves.front();
        if (peta_best.depth != 0)
            continue;

        const std::string sfen = bookminer::trim_sfen(bookminer::unpack_sfen_bytes(entry.key.bytes));
        const auto old_hit = find_book_position_with_flip(book, sfen);
        if (!old_hit.position.has_value() || old_hit.position->moves.empty())
            continue;

        const std::uint16_t peta_best_oriented_move16 = peta_best.move16;
        const auto old_best = best_moveinfo_in_sfen_orientation(*old_hit.position, old_hit.flipped);
        const auto* old_candidate = find_moveinfo_by_oriented_move(
            *old_hit.position,
            peta_best_oriented_move16,
            old_hit.flipped);

        if (old_best.info == nullptr || old_candidate == nullptr)
            continue;
        if (old_best.oriented_move16 == peta_best_oriented_move16)
            continue;
        const auto* peta_old_best = find_moveinfo_by_oriented_move(
            peta_position,
            old_best.oriented_move16,
            false);
        if (peta_old_best == nullptr)
            continue;
        if (static_cast<int>(peta_best.eval) - static_cast<int>(peta_old_best->eval) < eval_refutation_margin)
            continue;
        if (eval_limit.has_value() && std::abs(static_cast<int>(peta_best.eval)) > *eval_limit)
        {
            ++skipped_by_eval_limit;
            continue;
        }

        const std::string peta_best_move = bookminer::move16_to_usi(peta_best_oriented_move16);
        if (peta_best_move.empty())
            continue;

        const std::string sfen_with_ply = sfen + " " + std::to_string(peta_position.ply);
        append_unique_position_command(think_sfens, think_seen, "sfen " + sfen_with_ply + " moves " + peta_best_move);
    }

    const fs::path output_path = fs::path(BookDir) / ThinkSfensName;
    log_line("write book path = " + output_path.string() + ", len(think_sfens) = " + std::to_string(think_sfens.size()) + ".");
    write_position_commands_file(output_path, think_sfens);

    log_line("peta_refutation done.");
    return "[PetaRefutationDone] path=" + output_path.string()
        + " count=" + std::to_string(think_sfens.size())
        + " skipped_by_eval_limit=" + std::to_string(skipped_by_eval_limit)
        + " skipped_by_ply=" + std::to_string(skipped_by_ply);
}

bool is_text_book_path(const fs::path& path)
{
    auto extension = path.extension().native();
    for (auto& ch : extension)
        if (ch >= 'A' && ch <= 'Z') ch += 'a' - 'A';
    return extension == fs::path(".db").native();
}

bool is_supported_opponent_book_path(const fs::path& path)
{
    return is_text_book_path(path) || bookminer::is_yane_bin_book_path(path);
}

std::vector<fs::path> collect_opponent_book_paths()
{
    std::vector<fs::path> paths;
    const fs::path dir = BookOpponentDir;
    if (!fs::is_directory(dir))
        return paths;

    for (const auto& entry : fs::directory_iterator(dir))
        if (entry.is_regular_file() && is_supported_opponent_book_path(entry.path()))
            paths.push_back(entry.path());

    std::sort(paths.begin(), paths.end(), [](const fs::path& lhs, const fs::path& rhs) {
        return lhs.filename().string() < rhs.filename().string();
    });
    return paths;
}

struct CandidateBookMove {
    std::string move;
    int eval = 0;
};

std::vector<CandidateBookMove> candidate_best_moves_from_position(
    const bookminer::PositionInfo& position,
    bool flipped,
    int eval_diff)
{
    if (position.moves.empty())
        return {};

    int best_eval = std::numeric_limits<int>::min();
    bool has_best = false;
    for (const auto& moveinfo : position.moves)
    {
        best_eval = std::max(best_eval, static_cast<int>(moveinfo.eval));
        has_best = true;
    }
    if (!has_best)
        return {};

    std::vector<CandidateBookMove> moves;
    const std::int64_t eval_low = static_cast<std::int64_t>(best_eval) - eval_diff;
    for (const auto& moveinfo : position.moves)
    {
        const int eval = static_cast<int>(moveinfo.eval);
        if (eval < eval_low)
            continue;
        const std::uint16_t move16 = flipped ? bookminer::flipped_move16(moveinfo.move16) : moveinfo.move16;
        const std::string move = bookminer::move16_to_usi(move16);
        if (!move.empty())
            moves.push_back(CandidateBookMove{move, eval});
    }
    return moves;
}

std::vector<CandidateBookMove> candidate_best_moves_for_sfen(
    const bookminer::BookStore& peta_book,
    const std::string& sfen,
    int eval_diff)
{
    const auto hit = find_peta_position_with_flip(peta_book, sfen);
    if (hit.position == nullptr)
        return {};
    return candidate_best_moves_from_position(*hit.position, hit.flipped, eval_diff);
}

struct ProbePositionHit {
    std::optional<bookminer::PositionInfo> position;
    bool flipped = false;
};

ProbePositionHit find_probe_position_with_flip(bookminer::BookProbe& probe, const std::string& sfen)
{
    if (auto position = probe.find_position_copy(sfen))
        return {std::move(position), false};
    if (auto position = probe.find_position_copy(bookminer::flipped_sfen(sfen)))
        return {std::move(position), true};
    return {};
}

std::vector<CandidateBookMove> candidate_best_moves_for_probe(
    bookminer::BookProbe& probe,
    const std::string& sfen,
    int eval_diff)
{
    const auto hit = find_probe_position_with_flip(probe, sfen);
    if (!hit.position.has_value())
        return {};
    return candidate_best_moves_from_position(*hit.position, hit.flipped, eval_diff);
}

std::optional<CandidateBookMove> choose_random_candidate_move(
    const std::vector<CandidateBookMove>& moves,
    std::mt19937_64& rng)
{
    if (moves.empty())
        return std::nullopt;
    std::uniform_int_distribution<std::size_t> dist(0, moves.size() - 1);
    return moves[dist(rng)];
}

std::optional<std::string> peta_pv_leaf_position_command_from_position_random(
    const bookminer::BookStore& peta_book,
    const std::string& position_command,
    const std::string& sfen_with_ply,
    int max_book_ply,
    int max_step,
    int eval_diff,
    std::mt19937_64& rng,
    std::optional<int> eval_limit = std::nullopt)
{
    if (max_step <= 0)
        return std::nullopt;

    std::string current_command = position_command;
    std::string current_sfen_with_ply = sfen_with_ply;
    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> visited;

    int step = 0;
    while (true)
    {
        if (step >= max_step)
            return std::nullopt;

        const auto [current_sfen, current_ply] = bookminer::trim_sfen_ply(current_sfen_with_ply);
        if (current_ply >= max_book_ply)
            return std::nullopt;
        if (visited_peta_position(visited, current_sfen))
            return std::nullopt;
        visited.insert(bookminer::PackedSfen::from_sfen(current_sfen));

        const auto hit = find_peta_position_with_flip(peta_book, current_sfen);
        if (hit.position && peta_cut_by_eval_limit(*hit.position, hit.flipped, current_sfen_with_ply, eval_limit))
            return std::nullopt;
        const auto moves = candidate_best_moves_for_sfen(peta_book, current_sfen, eval_diff);
        const auto selected = choose_random_candidate_move(moves, rng);
        if (!selected.has_value())
            return current_command;

        auto board = bookminer::SfenPosition::from_sfen(current_sfen_with_ply);
        board.push_usi(selected->move);
        current_sfen_with_ply = board.sfen_with_ply();
        if (bookminer::trim_sfen_ply(current_sfen_with_ply).second >= max_book_ply)
            return std::nullopt;
        current_command = append_position_move(current_command, selected->move);
        ++step;
    }
}

std::string peta_opponent(
    const bookminer::BookStore& peta_book,
    int eval_diff,
    int max_book_ply,
    int max_step,
    std::optional<int> book_extend_ply,
    std::optional<int> eval_limit)
{
    log_line("peta_opponent, eval_diff = " + std::to_string(eval_diff)
        + ", max_book_ply = " + std::to_string(max_book_ply)
        + ", max_step = " + std::to_string(max_step)
        + ", book_extend_ply = " + (book_extend_ply.has_value() ? std::to_string(*book_extend_ply) : std::string("None"))
        + ", eval_limit = " + (eval_limit.has_value() ? std::to_string(*eval_limit) : std::string("None"))
        + ", opponent_dir = " + std::string(BookOpponentDir));

    const auto opponent_paths = collect_opponent_book_paths();
    if (opponent_paths.empty())
        throw std::runtime_error(std::string("opponent peta book file not found : ") + BookOpponentDir + "/*.db or *.ybb");

    const auto random_seed = static_cast<std::uint64_t>(
        std::chrono::high_resolution_clock::now().time_since_epoch().count());
    std::mt19937_64 rng(random_seed);
    log_line("[PetaOpponentRandomSeed] seed=" + std::to_string(random_seed));

    std::vector<PositionCommandEntry> think_sfens;
    std::unordered_map<std::string, std::size_t> think_indexes;
    std::size_t processed_nodes = 0;
    std::size_t cut_nodes = 0;
    std::size_t leaf_nodes = 0;
    std::size_t skipped_by_ply = 0;
    std::size_t retired_repetition = 0;
    std::size_t retired_max_step = 0;

    for (const auto& opponent_path : opponent_paths)
    {
        log_line("peta_opponent open opponent book probe : " + opponent_path.string());
        auto opponent_probe = bookminer::open_book_probe(opponent_path);

        for (const int current_side : {1, 0})
        {
            const std::string side_name = current_side == 1 ? "black" : "white";
            log_line("--- peta_opponent " + opponent_path.filename().string() + " current=" + side_name + " ---");

            std::string position_command = "startpos";
            std::string sfen_with_ply = bookminer::StartSfenPly1;
            std::unordered_set<std::string> visited;
            int step = 0;
            while (true)
            {
                if (step >= max_step)
                {
                    ++retired_max_step;
                    break;
                }

                ++processed_nodes;
                if (processed_nodes % PetaOpponentProgressInterval == 0)
                {
                    log_line("opponent progress nodes = " + std::to_string(processed_nodes)
                        + " , cuts = " + std::to_string(cut_nodes)
                        + " , think_sfens = " + std::to_string(think_sfens.size()));
                }

                const auto [sfen, ply] = bookminer::trim_sfen_ply(sfen_with_ply);
                if (ply >= max_book_ply)
                {
                    ++skipped_by_ply;
                    break;
                }

                const std::string visited_key = std::to_string(current_side) + ":" + sfen;
                const std::string visited_key_flipped = std::to_string(current_side) + ":" + bookminer::flipped_sfen(sfen);
                if (visited.find(visited_key) != visited.end() || visited.find(visited_key_flipped) != visited.end())
                {
                    ++retired_repetition;
                    break;
                }
                visited.insert(visited_key);

                const auto own_hit = find_peta_position_with_flip(peta_book, sfen);
                if (own_hit.position && peta_cut_by_eval_limit(*own_hit.position, own_hit.flipped, sfen_with_ply, eval_limit))
                    break;

                const auto moves = (ply % 2 == current_side)
                    ? candidate_best_moves_for_sfen(peta_book, sfen, eval_diff)
                    : candidate_best_moves_for_probe(*opponent_probe, sfen, eval_diff);
                if (moves.empty())
                {
                    ++cut_nodes;
                    const auto leaf = peta_pv_leaf_position_command_from_position_random(
                        peta_book,
                        position_command,
                        sfen_with_ply,
                        max_book_ply,
                        max_step,
                        eval_diff,
                        rng,
                        eval_limit);
                    if (leaf.has_value())
                    {
                        add_position_command_entry(
                            think_sfens,
                            think_indexes,
                            *leaf,
                            book_extend_ply,
                            eval_limit,
                            max_book_ply);
                        ++leaf_nodes;
                    }
                    break;
                }

                const auto selected = choose_random_candidate_move(moves, rng);
                if (!selected.has_value())
                    break;

                auto board = bookminer::SfenPosition::from_sfen(sfen_with_ply);
                board.push_usi(selected->move);
                sfen_with_ply = board.sfen_with_ply();
                if (bookminer::trim_sfen_ply(sfen_with_ply).second >= max_book_ply)
                {
                    ++skipped_by_ply;
                    break;
                }
                position_command = append_position_move(position_command, selected->move);
                ++step;
            }
        }
    }

    const fs::path output_path = fs::path(BookDir) / ThinkSfensName;
    log_line("write book path = " + output_path.string() + ", len(think_sfens) = " + std::to_string(think_sfens.size()) + ".");
    write_position_command_entries_file(output_path, think_sfens);

    log_line("peta_opponent done.");
    return "[PetaOpponentDone] path=" + output_path.string()
        + " count=" + std::to_string(think_sfens.size())
        + " opponent_books=" + std::to_string(opponent_paths.size())
        + " processed_nodes=" + std::to_string(processed_nodes)
        + " cut_nodes=" + std::to_string(cut_nodes)
        + " leaf_nodes=" + std::to_string(leaf_nodes)
        + " skipped_by_ply=" + std::to_string(skipped_by_ply)
        + " retired_repetition=" + std::to_string(retired_repetition)
        + " retired_max_step=" + std::to_string(retired_max_step);
}

fs::path executable_dir(const char* argv0)
{
#ifdef _WIN32
    char buffer[MAX_PATH]{};
    const DWORD size = GetModuleFileNameA(nullptr, buffer, MAX_PATH);
    if (size != 0)
        return fs::path(buffer).parent_path();
#else
    std::array<char, 4096> buffer{};
    const ssize_t size = readlink("/proc/self/exe", buffer.data(), buffer.size() - 1);
    if (size > 0)
    {
        buffer[static_cast<std::size_t>(size)] = '\0';
        return fs::path(buffer.data()).parent_path();
    }
#endif
    return fs::absolute(argv0).parent_path();
}

std::string progress_total_text(std::optional<std::size_t> total)
{
    return total.has_value() ? std::to_string(*total) : "?";
}

void book_read_progress(bookminer::BookProgressKind kind, std::size_t current, std::optional<std::size_t> total, const fs::path& path, void*)
{
    switch (kind)
    {
    case bookminer::BookProgressKind::Start:
        log_line("[BookReadStart] 0/" + progress_total_text(total) + " path=" + path.string());
        break;
    case bookminer::BookProgressKind::Progress:
        log_line("[BookReadProgress] " + std::to_string(current) + "/" + progress_total_text(total));
        break;
    case bookminer::BookProgressKind::Done:
        log_line("[BookReadDone] " + std::to_string(current) + "/" + progress_total_text(total) + " path=" + path.string());
        break;
    }
}

void book_write_progress(bookminer::BookProgressKind kind, std::size_t current, std::optional<std::size_t> total, const fs::path& path, void*)
{
    switch (kind)
    {
    case bookminer::BookProgressKind::Start:
        log_line("[BookWriteStart] 0/" + progress_total_text(total) + " path=" + path.string());
        break;
    case bookminer::BookProgressKind::Progress:
        log_line("[BookWriteProgress] " + std::to_string(current) + "/" + progress_total_text(total));
        break;
    case bookminer::BookProgressKind::Done:
        log_line("[BookWriteDone] " + std::to_string(current) + "/" + progress_total_text(total) + " path=" + path.string());
        break;
    }
}

std::string make_time_stamp()
{
    const auto now = std::chrono::system_clock::now();
    const std::time_t t = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char buffer[32]{};
    std::strftime(buffer, sizeof(buffer), "%Y%m%d%H%M%S", &tm);
    return buffer;
}

std::string scheduled_time_text(std::chrono::system_clock::time_point time)
{
    const std::time_t t = std::chrono::system_clock::to_time_t(time);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char buffer[32]{};
    std::strftime(buffer, sizeof(buffer), "%Y/%m/%d_%H:%M:%S", &tm);
    return buffer;
}

std::vector<fs::path> collect_book_backup_paths()
{
    std::vector<fs::path> paths;
    if (!fs::is_directory(BookBackupDir))
        return paths;

    for (const auto& entry : fs::directory_iterator(BookBackupDir))
    {
        if (!entry.is_regular_file())
            continue;
        const auto filename = entry.path().filename().string();
        if (filename.rfind(std::string(BookDbName) + "-", 0) != 0)
            continue;
        const bool is_text_book = is_text_book_path(entry.path());
        const bool is_ybb_book = bookminer::is_yane_bin_book_path(entry.path());
        if (!is_text_book && !is_ybb_book)
            continue;
        if (filename.find("_ply") != std::string::npos)
            continue;
        paths.push_back(entry.path());
    }

    std::sort(paths.begin(), paths.end(), [](const fs::path& lhs, const fs::path& rhs) {
        return lhs.filename().string() < rhs.filename().string();
    });
    return paths;
}

std::optional<fs::path> get_latest_book_backup_or_none()
{
    auto paths = collect_book_backup_paths();
    if (!paths.empty())
        return paths.back();

    fs::path legacy = fs::path(BookBackupDir) / (std::string(BookDbName) + ".db");
    if (fs::is_regular_file(legacy))
        return legacy;

    return std::nullopt;
}

std::vector<fs::path> collect_peta_book_paths()
{
    std::vector<fs::path> paths;
    if (!fs::is_directory(BookBackupDir))
        return paths;

    for (const auto& entry : fs::directory_iterator(BookBackupDir))
    {
        if (!entry.is_regular_file())
            continue;
        const auto filename = entry.path().filename().string();
        if (filename.rfind(std::string(PetaBookDbName) + "-", 0) != 0)
            continue;
        const bool is_text_book = is_text_book_path(entry.path());
        const bool is_ybb_book = bookminer::is_yane_bin_book_path(entry.path());
        if (!is_text_book && !is_ybb_book)
            continue;
        paths.push_back(entry.path());
    }

    std::sort(paths.begin(), paths.end(), [](const fs::path& lhs, const fs::path& rhs) {
        return lhs.filename().string() < rhs.filename().string();
    });
    return paths;
}

fs::path get_latest_peta_book()
{
    auto paths = collect_peta_book_paths();
    if (paths.empty())
        throw std::runtime_error(std::string("peta book file not found : ") + BookBackupDir + "/" + PetaBookDbName + "-*.db or " + PetaBookDbName + "-*.ybb");
    return paths.back();
}

bool is_supported_peta_book_path(const fs::path& path)
{
    return is_text_book_path(path) || bookminer::is_yane_bin_book_path(path);
}

void ensure_supported_peta_book_path(const fs::path& path, const std::string& label)
{
    if (!is_supported_peta_book_path(path))
        throw std::runtime_error(label + " must be .db or .ybb : " + path.string());
}

fs::path make_backup_path_with_timestamp(const std::string& timestamp, std::size_t position_count, std::optional<int> ply_limit)
{
    fs::create_directories(BookBackupDir);
    std::string filename = std::string(BookDbName) + "-" + timestamp + "_" + std::to_string(position_count);
    if (ply_limit.has_value())
        filename += "_ply" + std::to_string(*ply_limit);
    filename += ".ybb";
    return fs::path(BookBackupDir) / filename;
}

fs::path make_backup_saving_path(const std::string& timestamp, std::optional<int> ply_limit)
{
    fs::create_directories(BookBackupDir);
    std::string filename = "." + std::string(BookDbName) + "-" + timestamp + "_saving";
    if (ply_limit.has_value())
        filename += "_ply" + std::to_string(*ply_limit);
    filename += ".ybb";
    return fs::path(BookBackupDir) / filename;
}

std::optional<std::pair<std::string, std::size_t>> parse_regular_book_backup_name(const fs::path& path)
{
    const std::string filename = path.filename().string();
    const std::regex pattern("^" + std::string(BookDbName) + R"(-(\d{14})_(\d+)(?:\.db|\.ybb)$)", std::regex::icase);
    std::smatch match;
    if (!std::regex_match(filename, match, pattern))
        return std::nullopt;
    return std::make_pair(match[1].str(), static_cast<std::size_t>(std::stoull(match[2].str())));
}

fs::path peta_book_backup_path_from_source(const fs::path& source_book_path)
{
    const std::string extension = bookminer::is_yane_bin_book_path(source_book_path) ? ".ybb" : ".db";
    const auto parsed = parse_regular_book_backup_name(source_book_path);
    if (!parsed.has_value())
        return fs::path(BookBackupDir) / (std::string(PetaBookDbName) + "-" + make_time_stamp() + extension);

    const auto& [timestamp, position_count] = *parsed;
    return fs::path(BookBackupDir) / (std::string(PetaBookDbName) + "-" + timestamp + "_" + std::to_string(position_count) + extension);
}

fs::path resolve_peta_source_book_path(const std::optional<std::string>& path)
{
    if (!path.has_value())
    {
        auto latest = get_latest_book_backup_or_none();
        if (!latest.has_value())
            throw std::runtime_error(std::string("book backup file not found : ") + BookBackupDir + "/" + BookDbName + "-*.db or " + BookDbName + "-*.ybb");
        ensure_supported_peta_book_path(*latest, "peta source book");
        return *latest;
    }

    const std::vector<fs::path> candidates = {
        fs::path(*path),
        fs::path(BookDir) / *path,
    };

    for (const auto& candidate : candidates)
        if (fs::is_regular_file(candidate))
        {
            ensure_supported_peta_book_path(candidate, "peta source book");
            return candidate;
        }

    throw std::runtime_error("peta source book not found : " + *path);
}

fs::path resolve_peta_book_path(const std::optional<std::string>& path)
{
    if (!path.has_value())
        return get_latest_peta_book();

    const std::vector<fs::path> candidates = {
        fs::path(*path),
        fs::path(BookDir) / *path,
    };

    for (const auto& candidate : candidates)
        if (fs::is_regular_file(candidate))
        {
            ensure_supported_peta_book_path(candidate, "peta book");
            return candidate;
        }

    throw std::runtime_error("peta book not found : " + *path);
}

std::string to_book_dir_relative_path(const fs::path& path)
{
    const fs::path book_dir_abs = fs::absolute(BookDir).lexically_normal();
    const fs::path path_abs = fs::absolute(path).lexically_normal();
    const fs::path rel = path_abs.lexically_relative(book_dir_abs);
    if (rel.empty())
        throw std::runtime_error("failed to make BookDir relative path : " + path.string());

    const std::string rel_string = rel.generic_string();
    if (rel_string == ".." || rel_string.rfind("../", 0) == 0)
        throw std::runtime_error(std::string("peta source book must be under ") + BookDir + " : " + path.string());
    return rel_string;
}

fs::path path_from_utf8(const std::string& text)
{
    return fs::path(std::u8string(text.begin(), text.end()));
}

fs::path find_peta_shock_engine(const fs::path& app_dir, const bookminer::BookMinerSettings& settings)
{
    const fs::path configured = path_from_utf8(settings.peta_shock_engine_path);
    const fs::path engine_path = configured.is_absolute() ? configured : app_dir / configured;
    if (fs::is_regular_file(engine_path))
        return engine_path;
    throw std::runtime_error("peta shock engine not found : " + path_to_utf8(engine_path));
}

struct PetaProcessResult {
    int exit_code = 1;
    bool completed = false;
    bool reported_error = false;
    bool cancelled = false;
};

struct PetaShockCancelled {};

void check_peta_shock_cancel(const std::atomic<bool>* cancelled)
{
    if (cancelled && cancelled->load())
        throw PetaShockCancelled{};
}

void emit_prefixed_output_line(std::string line, PetaProcessResult& result)
{
    if (!line.empty() && line.back() == '\r')
        line.pop_back();
    if (line.find("Making a peta-shock book has been completed.") != std::string::npos)
        result.completed = true;
    if (line.find("Error!") != std::string::npos)
        result.reported_error = true;
    if (!line.empty() && !is_yaneuraou_progress_bar_line(line))
        log_line("[peta_shock] " + line);
}

void emit_output_chunk(std::string& pending, const char* data, std::size_t size, PetaProcessResult& result)
{
    pending.append(data, size);
    std::size_t pos = 0;
    while (true)
    {
        const auto newline_pos = pending.find('\n');
        const auto carriage_pos = pending.find('\r');
        if (newline_pos == std::string::npos && carriage_pos == std::string::npos)
            break;

        pos = newline_pos;
        if (carriage_pos != std::string::npos && (pos == std::string::npos || carriage_pos < pos))
            pos = carriage_pos;

        const char separator = pending[pos];
        std::string line = pending.substr(0, pos);
        pending.erase(0, pos + 1);
        if (separator == '\r' && !pending.empty() && pending.front() == '\n')
            pending.erase(0, 1);
        emit_prefixed_output_line(std::move(line), result);
    }
}

#ifdef _WIN32
std::wstring quote_windows_arg(const fs::path& path)
{
    const std::wstring s = path.wstring();
    std::wstring quoted = L"\"";
    for (wchar_t ch : s)
    {
        if (ch == L'"')
            quoted += L"\\\"";
        else
            quoted += ch;
    }
    quoted += L"\"";
    return quoted;
}

PetaProcessResult run_process_with_input(const fs::path& executable, const fs::path& cwd, const std::string& input, const std::atomic<bool>* cancelled = nullptr)
{
    constexpr int PetaShockProgressIntervalSeconds = 10;
    PetaProcessResult result;

    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;

    HANDLE child_stdin_read = nullptr;
    HANDLE child_stdin_write = nullptr;
    HANDLE child_stdout_read = nullptr;
    HANDLE child_stdout_write = nullptr;

    if (!CreatePipe(&child_stdin_read, &child_stdin_write, &sa, 0))
        throw std::runtime_error("CreatePipe stdin failed");
    if (!CreatePipe(&child_stdout_read, &child_stdout_write, &sa, 0))
    {
        CloseHandle(child_stdin_read);
        CloseHandle(child_stdin_write);
        throw std::runtime_error("CreatePipe stdout failed");
    }

    SetHandleInformation(child_stdin_write, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(child_stdout_read, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = child_stdin_read;
    si.hStdOutput = child_stdout_write;
    si.hStdError = child_stdout_write;

    PROCESS_INFORMATION pi{};
    std::wstring command_line = quote_windows_arg(executable);
    std::wstring cwd_string = cwd.wstring();

    if (!CreateProcessW(nullptr, command_line.data(), nullptr, nullptr, TRUE, 0, nullptr, cwd_string.c_str(), &si, &pi))
    {
        CloseHandle(child_stdin_read);
        CloseHandle(child_stdin_write);
        CloseHandle(child_stdout_read);
        CloseHandle(child_stdout_write);
        throw std::runtime_error("CreateProcess failed: " + path_to_utf8(executable));
    }

    CloseHandle(child_stdin_read);
    CloseHandle(child_stdout_write);

    std::size_t offset = 0;
    bool input_failed = false;
    while (offset < input.size())
    {
        DWORD written = 0;
        const auto remaining = std::min<std::size_t>(input.size() - offset, std::numeric_limits<DWORD>::max());
        if (!WriteFile(child_stdin_write, input.data() + offset, static_cast<DWORD>(remaining), &written, nullptr) || written == 0)
        {
            input_failed = true;
            break;
        }
        offset += written;
    }
    CloseHandle(child_stdin_write);
    if (input_failed)
    {
        TerminateProcess(pi.hProcess, 1);
        WaitForSingleObject(pi.hProcess, INFINITE);
        CloseHandle(child_stdout_read);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        check_peta_shock_cancel(cancelled);
        throw std::runtime_error("failed to send peta shock commands to the engine");
    }

    std::string pending;
    char buffer[4096];
    DWORD exit_code = STILL_ACTIVE;
    auto start_time = std::chrono::steady_clock::now();
    auto last_progress_time = start_time;

    while (true)
    {
        if (cancelled && cancelled->load())
        {
            TerminateProcess(pi.hProcess, 1);
            WaitForSingleObject(pi.hProcess, INFINITE);
            result.cancelled = true;
            break;
        }
        DWORD available = 0;
        if (!PeekNamedPipe(child_stdout_read, nullptr, 0, nullptr, &available, nullptr))
            break;

        while (available > 0)
        {
            const DWORD to_read = std::min<DWORD>(available, static_cast<DWORD>(sizeof(buffer)));
            DWORD read_size = 0;
            if (!ReadFile(child_stdout_read, buffer, to_read, &read_size, nullptr) || read_size == 0)
                break;
            emit_output_chunk(pending, buffer, read_size, result);
            available -= std::min(available, read_size);
        }

        if (WaitForSingleObject(pi.hProcess, 0) == WAIT_OBJECT_0)
        {
            GetExitCodeProcess(pi.hProcess, &exit_code);
            DWORD remaining = 0;
            if (!PeekNamedPipe(child_stdout_read, nullptr, 0, nullptr, &remaining, nullptr) || remaining == 0)
                break;
        }

        const auto now = std::chrono::steady_clock::now();
        if (now - last_progress_time >= std::chrono::seconds(PetaShockProgressIntervalSeconds))
        {
            const auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(now - start_time).count();
            log_line("[peta_shock] running... elapsed " + std::to_string(elapsed) + "s");
            last_progress_time = now;
        }

        WaitForSingleObject(pi.hProcess, 1000);
    }

    if (!pending.empty())
        emit_prefixed_output_line(std::move(pending), result);
    CloseHandle(child_stdout_read);

    if (exit_code == STILL_ACTIVE)
    {
        while (WaitForSingleObject(pi.hProcess, 200) == WAIT_TIMEOUT)
        {
            if (cancelled && cancelled->load())
            {
                TerminateProcess(pi.hProcess, 1);
                result.cancelled = true;
                WaitForSingleObject(pi.hProcess, INFINITE);
                break;
            }
        }
        GetExitCodeProcess(pi.hProcess, &exit_code);
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    result.exit_code = static_cast<int>(exit_code);
    return result;
}
#else
ssize_t write_peta_input_without_sigpipe(int fd, const char* data, std::size_t size)
{
    sigset_t blocked, old_mask, pending;
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGPIPE);
    if (pthread_sigmask(SIG_BLOCK, &blocked, &old_mask) != 0)
        throw std::runtime_error("failed to block SIGPIPE");
    sigpending(&pending);
    const bool already_pending = sigismember(&pending, SIGPIPE) == 1;
    const auto result = write(fd, data, size);
    const int saved_errno = errno;
    if (result < 0 && saved_errno == EPIPE && !already_pending)
    {
        sigpending(&pending);
        if (sigismember(&pending, SIGPIPE) == 1)
        {
            int received = 0;
            sigwait(&blocked, &received);
        }
    }
    pthread_sigmask(SIG_SETMASK, &old_mask, nullptr);
    errno = saved_errno;
    return result;
}

void set_nonblocking(int fd)
{
    const int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0)
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

PetaProcessResult run_process_with_input(const fs::path& executable, const fs::path& cwd, const std::string& input, const std::atomic<bool>* cancelled = nullptr)
{
    constexpr int PetaShockProgressIntervalSeconds = 10;
    PetaProcessResult process_result;

    int stdin_pipe[2]{};
    int stdout_pipe[2]{};
    if (pipe(stdin_pipe) != 0)
        throw std::runtime_error("stdin pipe failed");
    if (pipe(stdout_pipe) != 0)
    {
        close(stdin_pipe[0]);
        close(stdin_pipe[1]);
        throw std::runtime_error("stdout pipe failed");
    }

    const pid_t pid = fork();
    if (pid < 0)
    {
        close(stdin_pipe[0]);
        close(stdin_pipe[1]);
        close(stdout_pipe[0]);
        close(stdout_pipe[1]);
        throw std::runtime_error("fork failed");
    }

    if (pid == 0)
    {
        chdir(cwd.c_str());
        dup2(stdin_pipe[0], STDIN_FILENO);
        dup2(stdout_pipe[1], STDOUT_FILENO);
        dup2(stdout_pipe[1], STDERR_FILENO);

        close(stdin_pipe[0]);
        close(stdin_pipe[1]);
        close(stdout_pipe[0]);
        close(stdout_pipe[1]);

        execl(executable.c_str(), executable.filename().c_str(), static_cast<char*>(nullptr));
        _exit(127);
    }

    close(stdin_pipe[0]);
    close(stdout_pipe[1]);

    try
    {
        std::size_t offset = 0;
        while (offset < input.size())
        {
            const ssize_t written = write_peta_input_without_sigpipe(stdin_pipe[1], input.data() + offset, input.size() - offset);
            if (written < 0 && errno == EINTR)
                continue;
            if (written <= 0)
                throw std::runtime_error("failed to send peta shock commands to the engine");
            offset += static_cast<std::size_t>(written);
        }
    }
    catch (...)
    {
        close(stdin_pipe[1]);
        close(stdout_pipe[0]);
        kill(pid, SIGKILL);
        waitpid(pid, nullptr, 0);
        check_peta_shock_cancel(cancelled);
        throw;
    }
    close(stdin_pipe[1]);
    set_nonblocking(stdout_pipe[0]);

    std::string pending;
    bool output_eof = false;
    int status = 0;
    bool child_done = false;
    auto start_time = std::chrono::steady_clock::now();
    auto last_progress_time = start_time;

    while (!output_eof || !child_done)
    {
        if (cancelled && cancelled->load())
        {
            if (!child_done)
            {
                kill(pid, SIGKILL);
                waitpid(pid, &status, 0);
                child_done = true;
            }
            process_result.cancelled = true;
            break;
        }
        fd_set read_set;
        FD_ZERO(&read_set);
        if (!output_eof)
            FD_SET(stdout_pipe[0], &read_set);

        timeval timeout{};
        timeout.tv_sec = 1;
        timeout.tv_usec = 0;

        const int ready = output_eof ? 0 : select(stdout_pipe[0] + 1, &read_set, nullptr, nullptr, &timeout);
        if (ready > 0 && FD_ISSET(stdout_pipe[0], &read_set))
        {
            char buffer[4096];
            while (true)
            {
                const ssize_t n = read(stdout_pipe[0], buffer, sizeof(buffer));
                if (n > 0)
                {
                    emit_output_chunk(pending, buffer, static_cast<std::size_t>(n), process_result);
                    continue;
                }
                if (n == 0)
                    output_eof = true;
                break;
            }
        }

        const auto now = std::chrono::steady_clock::now();
        if (now - last_progress_time >= std::chrono::seconds(PetaShockProgressIntervalSeconds))
        {
            const auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(now - start_time).count();
            log_line("[peta_shock] running... elapsed " + std::to_string(elapsed) + "s");
            last_progress_time = now;
        }

        if (!child_done)
        {
            const pid_t result = waitpid(pid, &status, WNOHANG);
            if (result == pid)
                child_done = true;
        }

        if (output_eof && child_done)
            break;
    }

    close(stdout_pipe[0]);
    if (!pending.empty())
        emit_prefixed_output_line(std::move(pending), process_result);

    if (!child_done)
        waitpid(pid, &status, 0);

    if (WIFEXITED(status))
        process_result.exit_code = WEXITSTATUS(status);
    return process_result;
}
#endif

fs::path run_peta_shock_makebook(const fs::path& app_dir, const fs::path& source_book_path,
                                const bookminer::BookMinerSettings& settings, const std::atomic<bool>* cancelled = nullptr)
{
    const fs::path engine_path = find_peta_shock_engine(app_dir, settings);
    // The engine's makebook command checks suffixes case-sensitively. Use a
    // same-volume hard link when the selected input has an uppercase suffix.
    (void)to_book_dir_relative_path(source_book_path);
    struct SourceAlias {
        fs::path path;
        bool owned = false;
        ~SourceAlias() { if (owned) { std::error_code error; fs::remove(path, error); } }
    } source_alias;
    fs::path engine_source = source_book_path;
    const fs::path engine_extension = bookminer::is_yane_bin_book_path(source_book_path) ? ".ybb" : ".db";
    if (source_book_path.extension() != engine_extension)
    {
        source_alias.path = bookminer::temp_book_path(source_book_path.parent_path() / ("peta-source" + engine_extension.string()));
        source_alias.owned = true;
        std::error_code error;
        if (!fs::remove(source_alias.path, error) || error)
            throw std::runtime_error("failed to prepare peta source link: " + path_to_utf8(source_alias.path));
        source_alias.owned = false;
        fs::create_hard_link(source_book_path, source_alias.path, error);
        if (error)
            throw std::runtime_error("cannot create peta source hard link; rename the source extension to lowercase: "
                                     + path_to_utf8(source_book_path) + " : " + error.message());
        source_alias.owned = true;
        engine_source = source_alias.path;
    }
    const std::string source_book_rel = to_book_dir_relative_path(engine_source);
    const fs::path peta_path = peta_book_backup_path_from_source(source_book_path);
    fs::create_directories(BookBackupDir);
    const fs::path peta_temp_path = bookminer::temp_book_path(peta_path);
    const std::string peta_temp_rel = to_book_dir_relative_path(peta_temp_path);

    std::error_code ec;
    if (!fs::remove(peta_temp_path, ec) || ec)
        throw std::runtime_error("failed to prepare temporary peta book: " + path_to_utf8(peta_temp_path));

    std::string makebook_command = "makebook peta_shock " + source_book_rel + " " + peta_temp_rel;
    if (settings.peta_shock_threads != 1)
        makebook_command += " threads " + std::to_string(settings.peta_shock_threads);

    log_line("start peta_shock makebook");
    log_line("engine path = " + path_to_utf8(engine_path));
    log_line("source book = " + path_to_utf8(source_book_path));
    log_line("peta book   = " + path_to_utf8(peta_path));
    log_line("command     = " + makebook_command);

    const std::string commands =
        "setoption name BookDir value book\n"
        "setoption name BookFile value no_book\n"
        "setoption name FlippedBook value true\n"
        "setoption name USI_Hash value 1\n" +
        makebook_command + "\n"
        "quit\n";

    check_peta_shock_cancel(cancelled);
    PetaProcessResult result;
    try
    {
        result = run_process_with_input(engine_path, app_dir, commands, cancelled);
    }
    catch (...)
    {
        fs::remove(peta_temp_path, ec);
        throw;
    }
    if (result.cancelled || (cancelled && cancelled->load()))
    {
        fs::remove(peta_temp_path, ec);
        throw PetaShockCancelled{};
    }
    if (result.exit_code != 0 || result.reported_error || !result.completed)
    {
        fs::remove(peta_temp_path, ec);
        throw std::runtime_error("peta_shock makebook failed or did not report completion. return code = "
                                 + std::to_string(result.exit_code));
    }

    if (!fs::is_regular_file(peta_temp_path) || fs::file_size(peta_temp_path) == 0)
    {
        fs::remove(peta_temp_path, ec);
        throw std::runtime_error("peta_shock makebook failed. output file was not created : " + peta_temp_path.string());
    }

    try
    {
        bookminer::replace_book_file(peta_temp_path, peta_path);
    }
    catch (...)
    {
        fs::remove(peta_temp_path, ec);
        throw;
    }
    log_line("..peta_shock makebook has done, path = " + peta_path.string());
    return peta_path;
}

void shock_write_progress(bookminer::BookProgressKind kind, std::size_t current,
                          std::optional<std::size_t> total, const fs::path& path, void* user)
{
    if (kind != bookminer::BookProgressKind::Done)
        check_peta_shock_cancel(static_cast<const std::atomic<bool>*>(user));
    if (kind == bookminer::BookProgressKind::Progress && !total)
        return;
    book_write_progress(kind, current, total, path, nullptr);
}

fs::path save_book_backup(const bookminer::BookStore& book, std::optional<int> ply_limit,
                          const std::atomic<bool>* cancelled = nullptr)
{
    std::unique_lock save_lock(g_save_mutex, std::defer_lock);
    if (cancelled)
    {
        while (!save_lock.try_lock())
        {
            check_peta_shock_cancel(cancelled);
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }
    else
        save_lock.lock();
    const auto timestamp = make_time_stamp();
    const auto saving_path = make_backup_saving_path(timestamp, ply_limit);
    log_line("start save_book_backup , path = " + saving_path.string());
    check_peta_shock_cancel(cancelled);
    const auto written_count = book.save_yaneuraou_book(saving_path, ply_limit,
        cancelled ? shock_write_progress : book_write_progress, const_cast<std::atomic<bool>*>(cancelled));
    if (cancelled && cancelled->load())
    {
        std::error_code ec;
        fs::remove(saving_path, ec);
        throw PetaShockCancelled{};
    }
    auto path = make_backup_path_with_timestamp(timestamp, written_count, ply_limit);
    bookminer::replace_book_file(saving_path, path);
    log_line("..save_book_backup has done, " + std::to_string(written_count) + " positions.");
    return path;
}

class AutoSaveService {
public:
    AutoSaveService(const bookminer::BookStore& book, int interval_seconds)
        : book_(book)
        , interval_seconds_(std::max(1, interval_seconds))
    {
    }

    ~AutoSaveService()
    {
        stop();
    }

    void start()
    {
        std::chrono::system_clock::time_point next;
        {
            std::scoped_lock lock(mutex_);
            if (started_)
                return;
            started_ = true;
            next_backup_time_ = std::chrono::system_clock::now() + std::chrono::seconds(interval_seconds_);
            next = next_backup_time_;
        }
        log_line("[BackupServiceStarted] next=" + scheduled_time_text(next)
            + " interval=" + std::to_string(interval_seconds_));
        thread_ = std::thread([this] {
            run();
        });
    }

    void stop()
    {
        {
            std::scoped_lock lock(mutex_);
            stopping_ = true;
        }
        cv_.notify_all();
        if (thread_.joinable())
            thread_.join();
    }

    void reset_timer()
    {
        {
            std::scoped_lock lock(mutex_);
            if (!started_ || stopping_)
                return;
            next_backup_time_ = std::chrono::system_clock::now() + std::chrono::seconds(interval_seconds_);
        }
        cv_.notify_all();
    }

    void set_interval(int seconds)
    {
        {
            std::scoped_lock lock(mutex_);
            interval_seconds_ = std::max(1, seconds);
            if (started_ && !stopping_)
                next_backup_time_ = std::chrono::system_clock::now() + std::chrono::seconds(interval_seconds_);
        }
        cv_.notify_all();
    }

private:
    void run()
    {
        while (true)
        {
            std::chrono::system_clock::time_point next;
            {
                std::scoped_lock lock(mutex_);
                next = next_backup_time_;
            }
            std::unique_lock lock(mutex_);
            log_line("[BackupNext] next=" + scheduled_time_text(next)
                + " interval=" + std::to_string(interval_seconds_));
            if (cv_.wait_until(lock, next, [&] { return stopping_ || next_backup_time_ != next; }))
            {
                if (stopping_)
                    return;
                continue;
            }
            lock.unlock();

            try
            {
                log_line("[BackupStart]");
                save_book_backup(book_, std::nullopt);
                log_line("[BackupDone]");
            }
            catch (const std::exception& ex)
            {
                log_line(std::string("Exception : auto save failed: ") + ex.what());
            }

            {
                std::scoped_lock next_lock(mutex_);
                if (stopping_)
                    return;
                if (next_backup_time_ == next)
                    next_backup_time_ = std::chrono::system_clock::now() + std::chrono::seconds(interval_seconds_);
            }
        }
    }

    const bookminer::BookStore& book_;
    int interval_seconds_ = 0;
    std::thread thread_;
    std::mutex mutex_;
    std::condition_variable cv_;
    std::chrono::system_clock::time_point next_backup_time_{};
    bool stopping_ = false;
    bool started_ = false;
};

std::optional<fs::path> load_latest_book_backup(bookminer::BookStore& book)
{
    auto path = get_latest_book_backup_or_none();
    if (!path.has_value())
    {
        log_line("book backup file not found. start with empty book. dir = " + std::string(BookBackupDir));
        return std::nullopt;
    }

    log_line("start load_book , path = " + path->string() + ", fast = True");
    book.load_yaneuraou_book(*path, false, book_read_progress, nullptr);
    log_line("done.." + std::to_string(book.size()) + " positions.");
    return path;
}

void shock_read_progress(bookminer::BookProgressKind kind, std::size_t current,
                         std::optional<std::size_t> total, const fs::path& path, void* user)
{
    if (kind != bookminer::BookProgressKind::Done)
        check_peta_shock_cancel(static_cast<const std::atomic<bool>*>(user));
    book_read_progress(kind, current, total, path, nullptr);
}

void read_peta_book(bookminer::BookStore& peta_book, const std::optional<std::string>& peta_book_path,
                    const std::atomic<bool>* cancelled = nullptr)
{
    const fs::path peta_path = resolve_peta_book_path(peta_book_path);

    log_line("read peta shocked book , path = " + peta_path.string());
    check_peta_shock_cancel(cancelled);
    peta_book.load_yaneuraou_book(peta_path, false, cancelled ? shock_read_progress : book_read_progress,
                                 const_cast<std::atomic<bool>*>(cancelled));
    log_line("[PetaBookPositions] positions=" + std::to_string(peta_book.size()));
    log_line("reading the peta_book has done.");
}

fs::path make_peta_book(
    const fs::path& app_dir,
    const std::optional<std::string>& source_book_path,
    const bookminer::BookMinerSettings& settings,
    const std::atomic<bool>* cancelled = nullptr)
{
    const fs::path source = resolve_peta_source_book_path(source_book_path);
    return run_peta_shock_makebook(app_dir, source, settings, cancelled);
}

class PetaBookReadJob {
public:
    explicit PetaBookReadJob(bookminer::BookStore& peta_book)
        : peta_book_(peta_book)
    {
    }

    ~PetaBookReadJob()
    {
        join();
    }

    bool running() const
    {
        return running_.load();
    }

    bool start(
        std::optional<std::string> peta_book_path,
        std::string done_message,
        std::string done_tag)
    {
        if (running_.load())
            return false;
        if (thread_.joinable())
            thread_.join();

        shock_ = false;
        running_ = true;
        thread_ = std::thread([
            this,
            peta_book_path = std::move(peta_book_path),
            done_message = std::move(done_message),
            done_tag = std::move(done_tag)
        ] {
            bool succeeded = false;
            try
            {
                read_peta_book(peta_book_, peta_book_path);
                if (!done_message.empty())
                    log_line(done_message);
                succeeded = true;
            }
            catch (const std::exception& ex)
            {
                log_line(std::string("Exception : peta read failed: ") + ex.what());
            }
            running_ = false;
            if (succeeded)
            {
                if (!done_tag.empty())
                    log_line(done_tag);
            }
        });
        return true;
    }

    bool start_shock(std::function<fs::path(const std::atomic<bool>*)> prepare, std::string done_message)
    {
        if (running_.load())
            return false;
        if (thread_.joinable())
            thread_.join();
        cancelled_ = false;
        shock_ = true;
        running_ = true;
        thread_ = std::thread([this, prepare = std::move(prepare), done_message = std::move(done_message)] {
            bool succeeded = false, was_cancelled = false;
            std::optional<std::string> failure;
            try
            {
                const auto path = prepare(&cancelled_);
                check_peta_shock_cancel(&cancelled_);
                read_peta_book(peta_book_, std::optional<std::string>{path.string()}, &cancelled_);
                log_line(done_message);
                succeeded = true;
            }
            catch (const PetaShockCancelled&) { was_cancelled = true; }
            catch (const std::exception& ex) { failure = ex.what(); }
            shock_ = false;
            running_ = false;
            if (was_cancelled)
                log_line("[PetaShockCancelled]");
            else if (failure)
                log_line("Exception : peta shock failed: " + *failure);
            else if (succeeded)
                log_line("[PetaCommandDone]");
        });
        return true;
    }

    bool cancel_shock()
    {
        if (!running_.load() || !shock_.load())
            return false;
        cancelled_ = true;
        return true;
    }

    bool shock_running() const { return running_.load() && shock_.load(); }

    void remember_saved_source(const fs::path& path, std::uint64_t revision)
    {
        std::scoped_lock lock(saved_source_mutex_);
        saved_source_ = std::make_pair(path, revision);
    }

    std::optional<std::pair<fs::path, std::uint64_t>> take_saved_source()
    {
        std::scoped_lock lock(saved_source_mutex_);
        return std::exchange(saved_source_, std::nullopt);
    }

    void join()
    {
        if (thread_.joinable())
            thread_.join();
    }

private:
    bookminer::BookStore& peta_book_;
    std::atomic<bool> running_{false};
    std::atomic<bool> shock_{false};
    std::atomic<bool> cancelled_{false};
    std::mutex saved_source_mutex_;
    std::optional<std::pair<fs::path, std::uint64_t>> saved_source_;
    std::thread thread_;
};

class PetaCommandJob {
public:
    explicit PetaCommandJob(std::function<void(const std::string&)> completion_logger = log_line)
        : completion_logger_(std::move(completion_logger))
    {
    }

    ~PetaCommandJob()
    {
        join();
    }

    bool running() const
    {
        return running_.load();
    }

    bool start(std::function<std::string()> command, bool cancellable_next = false)
    {
        if (running_.load())
            return false;
        if (thread_.joinable())
            thread_.join();

        cancelled_ = false;
        cancellable_next_ = cancellable_next;
        running_ = true;
        thread_ = std::thread([this, command = std::move(command)] {
            std::string completion;
            try
            {
                completion = command();
            }
            catch (const PetaNextCancelled&)
            {
                completion = "[PetaNextCancelled]";
            }
            catch (const std::exception& ex)
            {
                completion = std::string("Exception : peta command failed: ") + ex.what();
            }
            cancellable_next_ = false;
            running_ = false;
            completion_logger_(completion);
        });
        return true;
    }

    void join()
    {
        if (thread_.joinable())
            thread_.join();
    }

    bool cancel_next()
    {
        if (!running_.load() || !cancellable_next_.load())
            return false;
        cancelled_ = true;
        return true;
    }

    const std::atomic<bool>* cancellation_flag() const { return &cancelled_; }

private:
    std::function<void(const std::string&)> completion_logger_;
    std::atomic<bool> running_{false};
    std::atomic<bool> cancellable_next_{false};
    std::atomic<bool> cancelled_{false};
    std::thread thread_;
};

class ConcurrentManualBackup {
public:
    ~ConcurrentManualBackup() { join(); }

    bool start(std::function<fs::path()> save)
    {
        if (running_.load())
            return false;
        join();
        running_ = true;
        try
        {
            thread_ = std::thread([this, save = std::move(save)] {
                std::optional<fs::path> result;
                std::optional<std::string> failure;
                try { result = save(); }
                catch (const std::exception& ex) { failure = ex.what(); }
                running_ = false;
                if (failure)
                    log_line("Exception : command failed, command=w : " + *failure);
                else
                {
                    log_line("write path = " + result->string());
                    log_line("..w command write has done. path = " + result->string());
                    log_line("[ManualBackupDone] path=" + result->string());
                }
            });
        }
        catch (...)
        {
            running_ = false;
            throw;
        }
        return true;
    }

    bool running() const { return running_.load(); }
    void join() { if (thread_.joinable()) thread_.join(); }

private:
    std::atomic<bool> running_{false};
    std::thread thread_;
};

void print_help()
{
    log_line("Help : ");
    log_line("  Q : quit");
    log_line("  ! : quit without saving");
    log_line("  W : write book backup        , w (ply_limit)");
    log_line("  E : enqueue positions        , e");
    log_line("  EB: enqueue branch positions , eb branch_width branch_depth");
    log_line("  T : think positions          , t (eval_limit) (max_book_ply) (think_command_ply)");
    log_line("  SD: set defaults             , sd eval_diff max_step game_ply_limit book_extend_ply eval_limit");
    log_line("  R : read peta shocked book , r (peta book path)");
    log_line("  P : write backup, make and read peta shocked book");
    log_line("  PL: make and read peta shocked book from latest backup");
    log_line("  cancel-peta-shock : cancel the running peta shock");
    log_line("  PN : peta_shock next         , pn (peta_eval_diff) (max_step) (game_ply_limit) (book_extend_ply) (eval_limit) (normal_fallback:0|1)");
    log_line("  PR : peta refutation         , pr (eval_refutation_margin) (peta_eval_diff) (max_step) (game_ply_limit) (book_extend_ply) (eval_limit)");
    log_line("  PJ : peta rejoin             , pj (eval_drop) (peta_eval_diff) (max_step) (game_ply_limit) (book_extend_ply) (eval_limit)");
    log_line("  PNF: peta next refutation   , pnf peta_eval_diff (max_book_ply) (max_step) (eval_refutation_margin)");
    log_line("  PF : peta refutation         , pf (eval_refutation_margin) (eval_limit) (max_book_ply)");
    log_line("  PU : peta unsolved           , pu (min_depths) (max_step) (game_ply_limit) (book_extend_ply) (eval_limit)");
    log_line("  PO : peta opponent           , po (eval_diff) (max_step) (game_ply_limit) (book_extend_ply) (eval_limit)");
    log_line("  H : Help");
}

std::vector<bookminer::EngineConfig> load_engine_settings_with_log()
{
    const auto settings = bookminer::load_engine_settings(EngineSettingsPath);
    if (settings.empty())
        log_line("engine settings file not found or empty. path = " + std::string(EngineSettingsPath));
    else
        log_line("read engine settings , path = " + std::string(EngineSettingsPath) + ", groups = " + std::to_string(settings.size()));
    return settings;
}

bool has_arg(int argc, char* argv[], const std::string& arg)
{
    for (int i = 1; i < argc; ++i)
        if (argv[i] == arg)
            return true;
    return false;
}

} // namespace

int main(int argc, char* argv[])
{
    configure_windows_console();

    const bool from_gui = has_arg(argc, argv, "--from_gui");
    fs::path app_dir = executable_dir(argv[0]);
    for (int i = 1; i < argc; ++i)
    {
        if (std::string_view(argv[i]) != "--work-dir")
            continue;
        if (i + 1 >= argc)
        {
            std::cerr << "--work-dir requires a directory path\n";
            return 2;
        }
        app_dir = fs::absolute(fs::path(argv[++i]));
        if (!fs::is_directory(app_dir))
        {
            std::cerr << "--work-dir is not a directory: " << app_dir.string() << '\n';
            return 2;
        }
    }
    if (!app_dir.empty())
        fs::current_path(app_dir);

    fs::create_directories(BookDir);
    fs::create_directories(BookBackupDir);
    fs::create_directories("log");
    fs::create_directories("settings");

    Logger logger;
    g_logger = &logger;
    logger.open();

    bookminer::BookStore book;
    bookminer::BookStore peta_book;
    PetaBookReadJob peta_book_read_job(peta_book);
    PetaCommandJob peta_command_job;
    ConcurrentManualBackup concurrent_manual_backup;
    std::vector<std::unique_ptr<bookminer::UsiEngine>> engines;
    std::unique_ptr<TaskWorkers> task_workers;
    std::unique_ptr<AutoSaveService> auto_save_service;
    int max_book_ply = 200;
    CommandDefaults command_defaults;
    std::optional<fs::path> clean_source_path;
    std::uint64_t clean_source_revision = 0;

    try
    {
        auto book_miner_settings = bookminer::load_book_miner_settings({
            BookMinerSettingsPath,
            BookMinerCppSettingsPath,
        });
        log_line(
            "BookMiner settings : auto_save_interval_seconds = " + std::to_string(book_miner_settings.auto_save_interval_seconds)
            + ", max_book_ply = " + std::to_string(book_miner_settings.max_book_ply)
            + ", consecutive_node_ratio = " + std::to_string(book_miner_settings.consecutive_node_ratio)
            + ", peta_next_start_sfens_path = " + book_miner_settings.peta_next_start_sfens_path);
        max_book_ply = book_miner_settings.max_book_ply;
        g_consecutive_node_ratio.store(book_miner_settings.consecutive_node_ratio);
        command_defaults.game_ply_limit = max_book_ply;

        log_line("[StartupStage] stage=book_read message=定跡DBを読み込み中");
        clean_source_path = load_latest_book_backup(book);
        clean_source_revision = book.revision();
        g_mining_stats.report(book.size());
        log_line("[StartupStage] stage=book_read_done message=定跡DB読み込み完了");

        log_line("[StartupStage] stage=engine_init message=エンジン起動中");
        engines = bookminer::initialize_engines(load_engine_settings_with_log(), app_dir, [](const std::string& message) {
            log_line(message);
        });
        log_line("[StartupStage] stage=engine_init_done message=エンジン起動完了");

        log_line("[StartupStage] stage=task_worker message=探索worker起動中");
        task_workers = std::make_unique<TaskWorkers>(book, engines, max_book_ply);
        task_workers->start();
        log_line("[StartupStage] stage=task_worker_done message=探索worker起動完了");

        log_line("[StartupStage] stage=backup_service message=自動保存サービス起動中");
        auto_save_service = std::make_unique<AutoSaveService>(book, book_miner_settings.auto_save_interval_seconds);
        auto_save_service->start();

        log_line("[CommandReady] message=コマンド受付を開始しました。");

        auto reject_if_peta_busy = [&]() {
            if (peta_book_read_job.running())
            {
                log_line("Error : peta book is loading. wait for peta read completion.");
                return true;
            }
            if (peta_command_job.running())
            {
                log_line("Error : peta command is running. wait for peta command completion.");
                return true;
            }
            return false;
        };

        std::string line;
        while (true)
        {
            if (!from_gui)
                logger.prompt();
            if (!std::getline(std::cin, line))
            {
                peta_book_read_job.cancel_shock();
                peta_command_job.cancel_next();
                break;
            }

            auto tokens = split_ws(line);
            if (tokens.empty())
                continue;

            std::string command = tokens[0];
            std::transform(command.begin(), command.end(), command.begin(), [](unsigned char ch) {
                return static_cast<char>(std::tolower(ch));
            });

            try
            {
            if (command == "h")
            {
                print_help();
            }
            else if (command == "cancel-peta-next")
            {
                if (!peta_command_job.cancel_next())
                    log_line("[PetaNextCancelIgnored] no peta next is running");
            }
            else if (command == "cancel-peta-shock")
            {
                if (!peta_book_read_job.cancel_shock())
                    log_line("[PetaShockCancelIgnored] no peta shock is running");
            }
            else if (command == "q")
            {
                log_line("quit");
                peta_book_read_job.cancel_shock();
                concurrent_manual_backup.join();
                const auto before_revision = book.revision();
                const auto path = save_book_backup(book, std::nullopt);
                if (book.revision() == before_revision)
                {
                    clean_source_path = path;
                    clean_source_revision = before_revision;
                }
                if (auto_save_service)
                    auto_save_service->stop();
                if (task_workers)
                    task_workers->stop(true);
                peta_command_job.join();
                peta_book_read_job.join();
                break;
            }
            else if (command == "!")
            {
                log_line("quit without saving");
                peta_book_read_job.cancel_shock();
                concurrent_manual_backup.join();
                if (auto_save_service)
                    auto_save_service->stop();
                if (task_workers)
                    task_workers->stop(true);
                peta_command_job.join();
                peta_book_read_job.join();
                break;
            }
            else if (command == "w")
            {
                std::optional<int> ply_limit;
                if (tokens.size() >= 2)
                    ply_limit = parse_integer(tokens[1]);
                if (concurrent_manual_backup.running())
                {
                    log_line("Exception : command failed, command=w : manual backup is already running");
                    continue;
                }
                if (!ply_limit.has_value() && auto_save_service)
                    auto_save_service->reset_timer();
                if (peta_book_read_job.shock_running())
                {
                    concurrent_manual_backup.start([&, ply_limit] {
                        const auto revision = book.revision();
                        const auto path = save_book_backup(book, ply_limit);
                        if (!ply_limit && book.revision() == revision)
                            peta_book_read_job.remember_saved_source(path, revision);
                        return path;
                    });
                    continue;
                }
                const auto before_revision = book.revision();
                const auto path = save_book_backup(book, ply_limit);
                if (!ply_limit.has_value() && book.revision() == before_revision)
                {
                    clean_source_path = path;
                    clean_source_revision = before_revision;
                }
                log_line("write path = " + path.string());
                log_line("..w command write has done. path = " + path.string());
                log_line("[ManualBackupDone] path=" + path.string());
            }
            else if (command == "reload-settings")
            {
                const auto updated = bookminer::load_book_miner_settings({BookMinerSettingsPath, BookMinerCppSettingsPath});
                book_miner_settings = updated;
                g_consecutive_node_ratio.store(updated.consecutive_node_ratio);
                max_book_ply = updated.max_book_ply;
                command_defaults.game_ply_limit = updated.max_book_ply;
                auto_save_service->set_interval(updated.auto_save_interval_seconds);
                log_line("[SettingsApplied]");
            }
            else if (command == "sd" || command == "set-default")
            {
                if (tokens.size() != 6)
                {
                    log_line("Usage : sd eval_diff max_step game_ply_limit book_extend_ply eval_limit");
                    continue;
                }
                CommandDefaults new_defaults;
                new_defaults.eval_diff = parse_integer(tokens[1]);
                new_defaults.max_step = parse_integer(tokens[2]);
                new_defaults.game_ply_limit = parse_integer(tokens[3]);
                new_defaults.book_extend_ply = parse_integer(tokens[4]);
                new_defaults.eval_limit = parse_integer(tokens[5]);
                if (new_defaults.eval_diff < 0)
                    log_line("Error : default eval_diff must be non-negative integer.");
                else if (new_defaults.max_step <= 0)
                    log_line("Error : default max_step must be positive integer.");
                else if (new_defaults.game_ply_limit <= 0)
                    log_line("Error : default game_ply_limit must be positive integer.");
                else if (new_defaults.book_extend_ply < 0)
                    log_line("Error : default book_extend_ply must be non-negative integer.");
                else if (new_defaults.eval_limit < 0)
                    log_line("Error : default eval_limit must be non-negative integer.");
                else
                {
                    command_defaults = new_defaults;
                    log_line("[DefaultSettings] eval_diff=" + std::to_string(command_defaults.eval_diff)
                        + " max_step=" + std::to_string(command_defaults.max_step)
                        + " game_ply_limit=" + std::to_string(command_defaults.game_ply_limit)
                        + " book_extend_ply=" + std::to_string(command_defaults.book_extend_ply)
                        + " eval_limit=" + std::to_string(command_defaults.eval_limit));
                }
            }
            else if (command == "job")
            {
                if (tokens.size() != 3 || (tokens[1] != "pause" && tokens[1] != "resume" && tokens[1] != "delete"))
                    log_line("Usage : job pause|resume|delete id");
                else if (task_workers)
                    task_workers->control_job(parse_integer(tokens[2]), tokens[1]);
            }
            else if (command == "e")
            {
                const std::string path = fs::path(BookDir).append(ThinkSfensName).string();
                if (tokens.size() > 1)
                    log_line("Usage : e");
                else if (!task_workers)
                    log_line("Error : task workers are not running.");
                else
                    task_workers->enqueue_position_commands(
                        path,
                        command_defaults.eval_limit,
                        command_defaults.game_ply_limit,
                        command_defaults.book_extend_ply);
            }
            else if (command == "eb")
            {
                const std::string path = fs::path(BookDir).append(ThinkSfensName).string();
                if (tokens.size() != 3)
                {
                    log_line("Usage : eb branch_width branch_depth");
                    continue;
                }
                const int branch_width = parse_integer(tokens[1]);
                const int branch_depth = parse_integer(tokens[2]);
                if (!task_workers)
                    log_line("Error : task workers are not running.");
                else if (branch_width <= 0)
                    log_line("Error : branch_width must be positive integer.");
                else if (branch_depth <= 0)
                    log_line("Error : branch_depth must be positive integer.");
                else
                    task_workers->enqueue_position_commands(
                        path,
                        command_defaults.eval_limit,
                        command_defaults.game_ply_limit,
                        command_defaults.book_extend_ply,
                        branch_width,
                        branch_depth);
            }
            else if (command == "t")
            {
                const std::string path = fs::path(BookDir).append(ThinkSfensName).string();
                const int task_eval_limit = parse_int_argument(tokens, 1, command_defaults.eval_limit);
                const int task_max_book_ply = parse_int_argument(tokens, 2, command_defaults.game_ply_limit);
                const int task_think_command_ply = parse_int_argument(tokens, 3, command_defaults.book_extend_ply);
                if (!task_workers)
                    log_line("Error : task workers are not running.");
                else if (task_eval_limit < 0)
                    log_line("Error : eval_limit must be non-negative integer.");
                else if (task_max_book_ply <= 0)
                    log_line("Error : max_book_ply must be positive integer.");
                else if (task_think_command_ply <= 0)
                    log_line("Error : think_command_ply must be positive integer.");
                else
                    task_workers->enqueue_position_commands(path, task_eval_limit, task_max_book_ply, task_think_command_ply);
            }
            else if (command == "p")
            {
                if (reject_if_peta_busy())
                    continue;

                if (const auto saved = peta_book_read_job.take_saved_source())
                {
                    clean_source_path = saved->first;
                    clean_source_revision = saved->second;
                }
                std::optional<fs::path> reused_source;
                const auto current_revision = book.revision();
                if (clean_source_path.has_value()
                    && fs::is_regular_file(*clean_source_path)
                    && clean_source_revision == current_revision)
                {
                    reused_source = *clean_source_path;
                    log_line("p command source book reused = " + reused_source->string());
                }
                else
                {
                    if (auto_save_service)
                        auto_save_service->reset_timer();
                }
                log_line("start p command : write backup, peta_shock, and read peta book.");
                const auto settings = book_miner_settings;
                if (!peta_book_read_job.start_shock(
                    [&, reused_source, settings](const std::atomic<bool>* cancelled) {
                        const auto revision = book.revision();
                        const auto source = reused_source ? *reused_source : save_book_backup(book, std::nullopt, cancelled);
                        if (!reused_source && book.revision() == revision)
                            peta_book_read_job.remember_saved_source(source, revision);
                        check_peta_shock_cancel(cancelled);
                        log_line("p command source book = " + source.string());
                        return make_peta_book(app_dir, std::optional<std::string>{source.string()}, settings, cancelled);
                    }, "..p command has done."))
                {
                    log_line("Error : peta book is loading. wait for peta read completion.");
                }
            }
            else if (command == "pl" || command == "peta_shock_latest")
            {
                if (reject_if_peta_busy())
                    continue;

                log_line("start pl command : peta_shock latest backup, and read peta book.");
                const auto settings = book_miner_settings;
                if (!peta_book_read_job.start_shock(
                    [&, settings](const std::atomic<bool>* cancelled) {
                        return make_peta_book(app_dir, std::nullopt, settings, cancelled);
                    }, "..pl command has done."))
                {
                    log_line("Error : peta book is loading. wait for peta read completion.");
                }
            }
            else if (command == "pn")
            {
                if (reject_if_peta_busy())
                    continue;

                const int peta_eval_diff = parse_int_argument(tokens, 1, command_defaults.eval_diff);
                const int max_step = parse_int_argument(tokens, 2, command_defaults.max_step);
                const int command_max_book_ply = parse_int_argument(tokens, 3, command_defaults.game_ply_limit);
                const int book_extend_ply = parse_int_argument(tokens, 4, command_defaults.book_extend_ply);
                const int eval_limit = parse_int_argument(tokens, 5, command_defaults.eval_limit);
                const int normal_fallback = parse_int_argument(tokens, 6, 0);
                if (normal_fallback != 0 && normal_fallback != 1)
                {
                    log_line("Error : normal book fallback must be 0 or 1.");
                    continue;
                }
                if (peta_eval_diff < 0)
                {
                    log_line("Error : peta_eval_diff must be non-negative integer.");
                    continue;
                }
                if (command_max_book_ply <= 0)
                {
                    log_line("Error : max_book_ply must be positive integer.");
                    continue;
                }
                if (max_step <= 0)
                {
                    log_line("Error : max_step must be positive integer.");
                    continue;
                }
                if (book_extend_ply < 0)
                {
                    log_line("Error : book_extend_ply must be non-negative integer.");
                    continue;
                }
                if (eval_limit < 0)
                {
                    log_line("Error : eval_limit must be non-negative integer.");
                    continue;
                }
                const fs::path start_sfens_path = path_from_utf8(book_miner_settings.peta_next_start_sfens_path);
                if (!peta_command_job.start([
                        &peta_book,
                        &book,
                        normal_fallback,
                        peta_eval_diff,
                        max_step,
                        command_max_book_ply,
                        start_sfens_path,
                        book_extend_ply,
                        eval_limit,
                        &peta_command_job
                    ] {
                        return peta_next(
                            nullptr,
                            peta_book,
                            peta_eval_diff,
                            max_step,
                            command_max_book_ply,
                            start_sfens_path,
                            std::nullopt,
                            book_extend_ply,
                            eval_limit,
                            normal_fallback ? &book : nullptr,
                            peta_command_job.cancellation_flag());
                    }, true))
                {
                    log_line("Error : peta command is running. wait for peta command completion.");
                }
            }
            else if (command == "pj" || command == "peta_rejoin" || command == "peta-rejoin")
            {
                if (reject_if_peta_busy())
                    continue;

                const bool has_eval_drop = tokens.size() >= 7;
                const std::optional<int> eval_drop = has_eval_drop
                    ? std::optional<int>(parse_int_argument(tokens, 1, PetaRejoinEvalDropDisabled))
                    : std::nullopt;
                const std::size_t base_index = has_eval_drop ? 2 : 1;
                const int peta_eval_diff = parse_int_argument(tokens, base_index, command_defaults.eval_diff);
                const int max_step = parse_int_argument(tokens, base_index + 1, command_defaults.max_step);
                const int command_max_book_ply = parse_int_argument(tokens, base_index + 2, command_defaults.game_ply_limit);
                const int book_extend_ply = parse_int_argument(tokens, base_index + 3, command_defaults.book_extend_ply);
                const int eval_limit = parse_int_argument(tokens, base_index + 4, command_defaults.eval_limit);
                if (peta_eval_diff < 0)
                {
                    log_line("Error : peta_eval_diff must be non-negative integer.");
                    continue;
                }
                if (command_max_book_ply <= 0)
                {
                    log_line("Error : max_book_ply must be positive integer.");
                    continue;
                }
                if (max_step <= 0)
                {
                    log_line("Error : max_step must be positive integer.");
                    continue;
                }
                if (book_extend_ply < 0)
                {
                    log_line("Error : book_extend_ply must be non-negative integer.");
                    continue;
                }
                if (eval_limit < 0)
                {
                    log_line("Error : eval_limit must be non-negative integer.");
                    continue;
                }
                const fs::path start_sfens_path = path_from_utf8(book_miner_settings.peta_next_start_sfens_path);
                if (!peta_command_job.start([
                        &peta_book,
                        peta_eval_diff,
                        max_step,
                        command_max_book_ply,
                        start_sfens_path,
                        book_extend_ply,
                        eval_limit,
                        eval_drop
                    ] {
                        return peta_rejoin(
                            peta_book,
                            peta_eval_diff,
                            max_step,
                            command_max_book_ply,
                            start_sfens_path,
                            book_extend_ply,
                            eval_limit,
                            eval_drop);
                    }))
                {
                    log_line("Error : peta command is running. wait for peta command completion.");
                }
            }
            else if (command == "pnf")
            {
                if (reject_if_peta_busy())
                    continue;

                if (tokens.size() < 2)
                {
                    log_line("Usage : pnf peta_eval_diff (max_book_ply) (max_step) (eval_refutation_margin)");
                }
                else
                {
                    const int peta_eval_diff = parse_int_argument(tokens, 1, PetaDefaultInfEvalDiff);
                    const int command_max_book_ply = parse_int_argument(tokens, 2, max_book_ply);
                    const int max_step = parse_int_argument(tokens, 3, PetaDefaultMaxStep);
                    const int eval_refutation_margin = parse_int_argument(tokens, 4, DefaultEvalRefutationMargin);
                    if (command_max_book_ply <= 0)
                    {
                        log_line("Error : max_book_ply must be positive integer.");
                        continue;
                    }
                    if (max_step <= 0)
                    {
                        log_line("Error : max_step must be positive integer.");
                        continue;
                    }
                    const fs::path start_sfens_path = path_from_utf8(book_miner_settings.peta_next_start_sfens_path);
                    if (!peta_command_job.start([
                            &book,
                            &peta_book,
                            peta_eval_diff,
                            max_step,
                            command_max_book_ply,
                            start_sfens_path,
                            eval_refutation_margin
                        ] {
                            return peta_next(
                                &book,
                                peta_book,
                                peta_eval_diff,
                                max_step,
                                command_max_book_ply,
                                start_sfens_path,
                                eval_refutation_margin,
                                std::nullopt,
                                std::nullopt);
                        }))
                    {
                        log_line("Error : peta command is running. wait for peta command completion.");
                    }
                }
            }
            else if (command == "pr")
            {
                if (reject_if_peta_busy())
                    continue;

                const int eval_refutation_margin = parse_int_argument(tokens, 1, DefaultEvalRefutationMargin);
                const int peta_eval_diff = parse_int_argument(tokens, 2, command_defaults.eval_diff);
                const int max_step = parse_int_argument(tokens, 3, command_defaults.max_step);
                const int command_max_book_ply = parse_int_argument(tokens, 4, command_defaults.game_ply_limit);
                const int book_extend_ply = parse_int_argument(tokens, 5, command_defaults.book_extend_ply);
                const int eval_limit = parse_int_argument(tokens, 6, command_defaults.eval_limit);
                if (peta_eval_diff < 0)
                {
                    log_line("Error : peta_eval_diff must be non-negative integer.");
                    continue;
                }
                if (eval_refutation_margin < 0)
                {
                    log_line("Error : eval_refutation_margin must be non-negative integer.");
                    continue;
                }
                if (max_step <= 0)
                {
                    log_line("Error : max_step must be positive integer.");
                    continue;
                }
                if (command_max_book_ply <= 0)
                {
                    log_line("Error : max_book_ply must be positive integer.");
                    continue;
                }
                if (book_extend_ply < 0)
                {
                    log_line("Error : book_extend_ply must be non-negative integer.");
                    continue;
                }
                if (eval_limit < 0)
                {
                    log_line("Error : eval_limit must be non-negative integer.");
                    continue;
                }
                const fs::path start_sfens_path = path_from_utf8(book_miner_settings.peta_next_start_sfens_path);
                if (!peta_command_job.start([
                        &book,
                        &peta_book,
                        peta_eval_diff,
                        max_step,
                        command_max_book_ply,
                        start_sfens_path,
                        eval_refutation_margin,
                        book_extend_ply,
                        eval_limit
                    ] {
                        return peta_next(
                            &book,
                            peta_book,
                            peta_eval_diff,
                            max_step,
                            command_max_book_ply,
                            start_sfens_path,
                            eval_refutation_margin,
                            book_extend_ply,
                            eval_limit);
                    }))
                {
                    log_line("Error : peta command is running. wait for peta command completion.");
                }
            }
            else if (command == "pf")
            {
                if (reject_if_peta_busy())
                    continue;

                const int eval_refutation_margin = parse_int_argument(tokens, 1, DefaultEvalRefutationMargin);
                std::optional<int> refutation_eval_limit = parse_optional_int_argument(tokens, 2);
                const int command_max_book_ply = parse_int_argument(tokens, 3, max_book_ply);
                if (command_max_book_ply <= 0)
                {
                    log_line("Error : max_book_ply must be positive integer.");
                    continue;
                }
                if (!peta_command_job.start([
                        &book,
                        &peta_book,
                        eval_refutation_margin,
                        refutation_eval_limit,
                        command_max_book_ply
                    ] {
                        return peta_refutation(
                            book,
                            peta_book,
                            eval_refutation_margin,
                            refutation_eval_limit,
                            command_max_book_ply);
                    }))
                {
                    log_line("Error : peta command is running. wait for peta command completion.");
                }
            }
            else if (command == "pu")
            {
                if (reject_if_peta_busy())
                    continue;

                std::vector<int> min_depths;
                const std::string depth_text = tokens.size() > 1 && !is_none_argument(tokens[1]) ? tokens[1] : "10,8,6,4";
                std::istringstream depth_stream(depth_text);
                std::string depth_part;
                bool invalid_depths = depth_text.empty() || depth_text.back() == ',';
                while (std::getline(depth_stream, depth_part, ','))
                {
                    try {
                        if (depth_part.empty() || depth_part.find_first_not_of("0123456789") != std::string::npos)
                            throw std::invalid_argument("depth");
                        min_depths.push_back(parse_integer(depth_part));
                    } catch (const std::exception&) { invalid_depths = true; break; }
                }
                if (invalid_depths || min_depths.empty())
                {
                    log_line("Error : min_depths must be comma-separated non-negative integers.");
                    continue;
                }
                const int max_step = parse_int_argument(tokens, 2, command_defaults.max_step);
                const int command_max_book_ply = parse_int_argument(tokens, 3, command_defaults.game_ply_limit);
                const int book_extend_ply = parse_int_argument(tokens, 4, command_defaults.book_extend_ply);
                const int eval_limit = parse_int_argument(tokens, 5, command_defaults.eval_limit);
                if (command_max_book_ply <= 0)
                {
                    log_line("Error : max_book_ply must be positive integer.");
                    continue;
                }
                if (max_step <= 0)
                {
                    log_line("Error : max_step must be positive integer.");
                    continue;
                }
                if (book_extend_ply < 0)
                {
                    log_line("Error : book_extend_ply must be non-negative integer.");
                    continue;
                }
                if (eval_limit < 0)
                {
                    log_line("Error : eval_limit must be non-negative integer.");
                    continue;
                }
                if (!peta_command_job.start([
                        &peta_book,
                        min_depths,
                        command_max_book_ply,
                        max_step,
                        book_extend_ply,
                        eval_limit
                    ] {
                        return peta_unsolved(
                            peta_book,
                            min_depths,
                            command_max_book_ply,
                            max_step,
                            book_extend_ply,
                            eval_limit);
                    }))
                {
                    log_line("Error : peta command is running. wait for peta command completion.");
                }
            }
            else if (command == "po")
            {
                if (reject_if_peta_busy())
                    continue;

                const int eval_diff = parse_int_argument(tokens, 1, command_defaults.eval_diff);
                const int max_step = parse_int_argument(tokens, 2, command_defaults.max_step);
                const int command_max_book_ply = parse_int_argument(tokens, 3, command_defaults.game_ply_limit);
                const int book_extend_ply = parse_int_argument(tokens, 4, command_defaults.book_extend_ply);
                const int eval_limit = parse_int_argument(tokens, 5, command_defaults.eval_limit);
                if (eval_diff < 0)
                {
                    log_line("Error : eval_diff must be non-negative integer.");
                    continue;
                }
                if (command_max_book_ply <= 0)
                {
                    log_line("Error : max_book_ply must be positive integer.");
                    continue;
                }
                if (max_step <= 0)
                {
                    log_line("Error : max_step must be positive integer.");
                    continue;
                }
                if (book_extend_ply < 0)
                {
                    log_line("Error : book_extend_ply must be non-negative integer.");
                    continue;
                }
                if (eval_limit < 0)
                {
                    log_line("Error : eval_limit must be non-negative integer.");
                    continue;
                }
                if (!peta_command_job.start([
                        &peta_book,
                        eval_diff,
                        command_max_book_ply,
                        max_step,
                        book_extend_ply,
                        eval_limit
                    ] {
                        return peta_opponent(
                            peta_book,
                            eval_diff,
                            command_max_book_ply,
                            max_step,
                            book_extend_ply,
                            eval_limit);
                    }))
                {
                    log_line("Error : peta command is running. wait for peta command completion.");
                }
            }
            else if (command == "r")
            {
                if (reject_if_peta_busy())
                    continue;

                std::optional<std::string> peta_book_path;
                if (tokens.size() >= 2)
                    peta_book_path = tokens[1];
                if (!peta_book_read_job.start(std::move(peta_book_path), "", "[PetaReadDone]"))
                    log_line("Error : peta book is loading. wait for peta read completion.");
            }
            else
            {
                log_line("unknown command: " + command);
            }
            }
            catch (const std::exception& ex)
            {
                log_line("Exception : command failed, command=" + command + " : " + ex.what());
            }
        }
    }
    catch (const std::exception& ex)
    {
        log_line(std::string("Exception : ") + ex.what());
        return 1;
    }

    return 0;
}
