#include "book_store.h"
#include "config.h"
#include "sfen_position.h"
#include "usi_engine.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cctype>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <ctime>
#include <deque>
#include <filesystem>
#include <fstream>
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
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <fcntl.h>
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
constexpr const char* PetaShockEngineName = "YO-MATERIAL.exe";
constexpr const char* ThinkSfensName = "think_sfens.txt";
constexpr const char* ThinkUnsolvedSfensName = "think_unsolved_sfens.txt";
constexpr const char* EngineSettingsPath = "settings/engine_settings.json5";
constexpr const char* BookMinerSettingsPath = "settings/book_miner_settings.json5";
constexpr const char* BookMinerCppSettingsPath = "settings/book_miner_cpp_settings.json5";
constexpr int ThinkCommandPly = 6;
constexpr int DefaultEvalLimit = 400;
constexpr int PlyMin = std::numeric_limits<int>::min();
constexpr int DefaultEvalRefutationMargin = 100;
constexpr double DefaultDepthGapEvalPerPly = 0.1;
constexpr int PetaDefaultInfEvalDiff = 99999;
constexpr int PetaDefaultMaxStep = 9999;
constexpr int PetaDepthGapMaxBestDepth = 1000;
constexpr std::size_t PetaRefutationProgressInterval = 100000;
constexpr std::size_t PetaDepthGapProgressInterval = 100000;
constexpr std::size_t PetaUnsolvedProgressInterval = 100000;
constexpr std::size_t PetaOpponentProgressInterval = 100000;
constexpr int PetaOpponentDefaultEvalDiff = 0;
constexpr const char* BookOpponentDir = "book/book_opponent";

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

int parse_int_argument(const std::vector<std::string>& tokens, std::size_t index, int default_value)
{
    if (tokens.size() <= index || is_none_argument(tokens[index]))
        return default_value;
    return std::stoi(tokens[index]);
}

std::optional<int> parse_optional_int_argument(const std::vector<std::string>& tokens, std::size_t index)
{
    if (tokens.size() <= index || is_none_argument(tokens[index]))
        return std::nullopt;
    return std::stoi(tokens[index]);
}

double parse_double_argument(const std::vector<std::string>& tokens, std::size_t index, double default_value)
{
    if (tokens.size() <= index || is_none_argument(tokens[index]))
        return default_value;
    return std::stod(tokens[index]);
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

std::optional<int> get_move_eval(const bookminer::PositionInfo& position, const std::string& move)
{
    const auto move16 = bookminer::move16_from_usi(move);
    if (move16 == 0)
        return std::nullopt;

    for (const auto& move_info : position.moves)
        if (move_info.move16 == move16)
            return static_cast<int>(move_info.eval);
    return std::nullopt;
}

std::optional<int> get_book_move_eval(const bookminer::BookStore& book, const std::string& sfen, const std::string& move)
{
    auto lookup = find_book_position_with_flip(book, sfen);
    if (!lookup.position.has_value())
        return std::nullopt;

    const std::uint16_t move16 = bookminer::move16_from_usi(move);
    if (move16 == 0)
        return std::nullopt;

    const std::string book_move = bookminer::move16_to_usi(lookup.flipped ? bookminer::flipped_move16(move16) : move16);
    return get_move_eval(*lookup.position, book_move);
}

struct ThinkOnceResult {
    bookminer::PositionInfo position;
    std::string sfen;
};

std::optional<ThinkOnceResult> think_sfen_once(
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
        return std::nullopt;
    }

    const auto sfen_key = bookminer::PackedSfen::from_sfen(sfen);
    const auto sfen_f_key = sfen_key.flipped();
    if (visited.find(sfen_key) != visited.end() || visited.find(sfen_f_key) != visited.end())
        return std::nullopt;

    const std::string sfen_f = bookminer::flipped_sfen(sfen);
    bookminer::SearchLease lease;
    while (true)
    {
        lease = book.try_begin_search(sfen, sfen_f);
        if (lease.acquired)
            break;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }

    visited.insert(sfen_key);
    visited.insert(sfen_f_key);

    struct LeaseGuard {
        bookminer::BookStore& book;
        bookminer::SearchLease& lease;
        ~LeaseGuard() { book.end_search(lease); }
    } guard{book, lease};

    if (has_considered(lease.position))
        return ThinkOnceResult{*lease.position, lease.sfen};

    const double node_ratio = last_thinking_ply + 1 == ply ? 0.7 : 1.0;
    {
        std::ostringstream oss;
        oss << "[" << engine.thread_id() << "] " << lease.sfen << " " << ply << " , " << node_ratio;
        log_line(oss.str());
    }

    const auto engine_moves = engine.go(lease.sfen, node_ratio, [](const std::string& message) {
        log_line(message);
    });
    last_thinking_ply = ply;

    book.merge_position(lease.sfen, static_cast<std::uint16_t>(std::max(0, ply)), to_book_moves(engine_moves));
    log_line("[MiningProgress] positions=" + std::to_string(book.size()));

    auto position = book.find_position_copy(lease.sfen);
    if (has_considered(position))
        return ThinkOnceResult{*position, lease.sfen};
    return std::nullopt;
}

void start_thinking_best_line(
    bookminer::BookStore& book,
    bookminer::UsiEngine& engine,
    const std::string& leaf_sfen,
    int leaf_ply,
    int eval_limit,
    int max_book_ply,
    int think_command_ply)
{
    std::string current_sfen = leaf_sfen;
    int current_ply = leaf_ply;
    int rest_ply = think_command_ply;
    int last_thinking_ply = PlyMin;
    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> visited;

    while (rest_ply > 0)
    {
        auto thought = think_sfen_once(book, engine, current_sfen, current_ply, last_thinking_ply, max_book_ply, visited);
        if (!thought.has_value())
            return;
        current_sfen = thought->sfen;

        const auto best = get_best(thought->position);
        if (!best.has_value())
            return;
        if (std::abs(best->first) > eval_limit)
            return;

        bool moved = false;
        auto board = bookminer::SfenPosition::from_sfen(current_sfen + " " + std::to_string(current_ply));
        for (const auto& move_info : thought->position.moves)
        {
            if (std::abs(static_cast<int>(move_info.eval)) > eval_limit)
                continue;
            board.push_usi(bookminer::move16_to_usi(move_info.move16));
            current_sfen = board.sfen();
            current_ply = board.ply();
            --rest_ply;
            moved = true;
            break;
        }
        if (!moved)
            return;
    }
}

void process_position_command(
    bookminer::BookStore& book,
    bookminer::UsiEngine& engine,
    const std::string& position_command,
    int eval_limit,
    int max_book_ply,
    int think_command_ply)
{
    const auto parsed = bookminer::parse_position_command(position_command);
    auto board = bookminer::SfenPosition::from_sfen(parsed.start_sfen_with_ply);

    engine.usinewgame([](const std::string& message) {
        log_line(message);
    });

    int last_thinking_ply = PlyMin;
    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> visited;

    for (const auto& move : parsed.moves)
    {
        const auto current_sfen = board.sfen();
        const int ply = board.ply();

        auto current_lookup = find_book_position_with_flip(book, current_sfen);
        if (!has_considered(current_lookup.position))
        {
            auto thought = think_sfen_once(book, engine, current_sfen, ply, last_thinking_ply, max_book_ply, visited);
            if (!thought.has_value())
                return;
            current_lookup.position = thought->position;
            current_lookup.flipped = thought->sfen != current_sfen;
        }

        auto lookahead = board;
        lookahead.push_usi(move);
        const auto next_sfen = lookahead.sfen();
        const auto next_lookup = find_book_position_with_flip(book, next_sfen);

        if (!has_considered(next_lookup.position) && current_lookup.position.has_value())
        {
            const auto move_eval = get_book_move_eval(book, current_sfen, move);
            if (move_eval.has_value() && std::abs(*move_eval) > eval_limit)
                return;
        }

        board.push_usi(move);
    }

    start_thinking_best_line(book, engine, board.sfen(), board.ply(), eval_limit, max_book_ply, think_command_ply);
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
                const int parsed = std::stoi(value);
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
                const int parsed = std::stoi(value);
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
                const int parsed = std::stoi(value);
                if (parsed <= 0)
                    throw std::runtime_error("game_ply_limit must be positive integer or None");
                entry.max_book_ply = parsed;
            }
        }
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

std::vector<PositionCommandEntry> read_position_commands_file(const std::filesystem::path& path)
{
    std::ifstream in(path);
    if (!in)
        throw std::runtime_error("failed to open think_sfens file: " + path.string());

    std::vector<PositionCommandEntry> commands;
    std::unordered_map<std::string, std::size_t> indexes;
    std::string line;
    while (std::getline(in, line))
    {
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (line.empty() || line[0] == '#')
            continue;
        auto entry = parse_position_command_entry(line);
        add_position_command_entry(
            commands,
            indexes,
            entry.position_command,
            entry.book_extend_ply,
            entry.eval_limit,
            entry.max_book_ply);
    }
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
};

class TaskQueue {
public:
    void push(Task task)
    {
        {
            std::scoped_lock lock(mutex_);
            queue_.push_back(std::move(task));
        }
        cv_.notify_one();
    }

    std::optional<Task> pop()
    {
        std::unique_lock lock(mutex_);
        cv_.wait(lock, [&] {
            return stopping_ || !queue_.empty();
        });
        if (queue_.empty())
            return std::nullopt;

        auto task = std::move(queue_.front());
        queue_.pop_front();
        return task;
    }

    std::size_t size() const
    {
        std::scoped_lock lock(mutex_);
        return queue_.size();
    }

    void stop(bool discard_pending)
    {
        {
            std::scoped_lock lock(mutex_);
            stopping_ = true;
            if (discard_pending)
                queue_.clear();
        }
        cv_.notify_all();
    }

private:
    mutable std::mutex mutex_;
    std::condition_variable cv_;
    std::deque<Task> queue_;
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
        for (auto& engine : engines_)
        {
            threads_.emplace_back([this, engine_ptr = engine.get()] {
                worker_loop(*engine_ptr);
            });
        }
    }

    int enqueue_position_commands(const std::filesystem::path& path, int eval_limit, int max_book_ply, int think_command_ply)
    {
        const auto commands = read_position_commands_file(path);
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

        std::size_t total_taken = 0;
        std::size_t total_enqueued = 0;
        {
            std::scoped_lock lock(progress_mutex_);
            JobProgress progress;
            progress.total = added;
            progress.eval_limit = job_eval_limit;
            progress.game_ply_limit = job_game_ply_limit;
            progress.book_extend_ply = job_book_extend_ply;
            jobs_[job_id] = std::move(progress);
            total_enqueued_ += added;
            total_taken = total_taken_;
            total_enqueued = total_enqueued_;
        }

        log_line("(" + std::to_string(job_id) + ") put position commands , path = " + path.string()
            + " , eval_limit = " + std::to_string(eval_limit)
            + ", max_book_ply = " + std::to_string(max_book_ply)
            + ", think_command_ply = " + std::to_string(think_command_ply));
        log_line("(" + std::to_string(job_id) + ") read " + std::to_string(added) + " position commands.");
        log_line("[TaskQueueStart] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
            + " job=" + std::to_string(job_id)
            + " job_progress=0/" + std::to_string(added)
            + " job_remaining=" + std::to_string(added)
            + " added=" + std::to_string(added)
            + " remaining=" + std::to_string(total_enqueued - total_taken)
            + " path=" + path.string()
            + " eval_limit=" + job_eval_limit
            + " game_ply_limit=" + job_game_ply_limit
            + " book_extend_ply=" + job_book_extend_ply);

        for (const auto& command : commands)
        {
            const int task_eval_limit = command.eval_limit.value_or(eval_limit);
            const int task_max_book_ply = command.max_book_ply.value_or(max_book_ply);
            const int task_think_command_ply = command.book_extend_ply.value_or(think_command_ply);
            queue_.push(Task{command.position_command, task_eval_limit, task_max_book_ply, task_think_command_ply, job_id});
        }

        if (commands.empty())
            report_task_queue_done(job_id);

        return job_id;
    }

    void stop(bool discard_pending)
    {
        if (stopped_.exchange(true))
            return;

        queue_.stop(discard_pending);
        for (auto& thread : threads_)
        {
            if (thread.joinable())
                thread.join();
        }
    }

private:
    struct JobProgress {
        std::size_t total = 0;
        std::size_t taken = 0;
        bool done_reported = false;
        std::string eval_limit;
        std::string game_ply_limit;
        std::string book_extend_ply;
    };

    void worker_loop(bookminer::UsiEngine& engine)
    {
        while (true)
        {
            auto task = queue_.pop();
            if (!task.has_value())
                return;

            report_task_taken(*task);

            try
            {
                process_position_command(
                    book_,
                    engine,
                    task->position_command,
                    task->eval_limit,
                    task->max_book_ply,
                    task->think_command_ply);
            }
            catch (const std::exception& ex)
            {
                log_line(std::string("Exception : ") + ex.what());
            }
        }
    }

    void report_task_taken(const Task& task)
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

        const auto now = std::chrono::steady_clock::now();
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
            ++job.taken;
            job_taken = job.taken;
            job_total = job.total;
            job_eval_limit = job.eval_limit;
            job_game_ply_limit = job.game_ply_limit;
            job_book_extend_ply = job.book_extend_ply;
            should_report_job_done = job.total > 0 && job.taken >= job.total && !job.done_reported;
            if (should_report_job_done)
                job.done_reported = true;

            const auto remaining = total_enqueued > total_taken ? total_enqueued - total_taken : 0;
            if (last_task_progress_report_.time_since_epoch().count() == 0
                || now - last_task_progress_report_ >= std::chrono::seconds(10)
                || remaining == 0
                || should_report_job_done)
            {
                should_report = true;
                last_task_progress_report_ = now;
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
            + " eval_limit=" + job_eval_limit
            + " game_ply_limit=" + job_game_ply_limit
            + " book_extend_ply=" + job_book_extend_ply);

        if (should_report_job_done)
        {
            log_line("[TaskQueueJobDone] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
                + " job=" + std::to_string(task.job_id)
                + " job_progress=" + std::to_string(job_taken) + "/" + std::to_string(job_total)
                + " job_remaining=0"
                + " remaining=" + std::to_string(remaining)
                + " eval_limit=" + job_eval_limit
                + " game_ply_limit=" + job_game_ply_limit
                + " book_extend_ply=" + job_book_extend_ply);
        }

        if (remaining == 0)
        {
            log_line("[TaskQueueDone] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
                + " job=" + std::to_string(task.job_id)
                + " job_progress=" + std::to_string(job_taken) + "/" + std::to_string(job_total)
                + " job_remaining=" + std::to_string(job_remaining)
                + " remaining=0"
                + " eval_limit=" + job_eval_limit
                + " game_ply_limit=" + job_game_ply_limit
                + " book_extend_ply=" + job_book_extend_ply);
        }
    }

    void report_task_queue_done(int job_id)
    {
        std::size_t total_taken = 0;
        std::size_t total_enqueued = 0;
        std::string job_eval_limit = "-";
        std::string job_game_ply_limit = "-";
        std::string job_book_extend_ply = "-";
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
            }
        }
        log_line("[TaskQueueJobDone] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
            + " job=" + std::to_string(job_id)
            + " job_progress=0/0 job_remaining=0 remaining=" + std::to_string(total_enqueued - total_taken)
            + " eval_limit=" + job_eval_limit
            + " game_ply_limit=" + job_game_ply_limit
            + " book_extend_ply=" + job_book_extend_ply);
        if (total_taken >= total_enqueued)
        {
            log_line("[TaskQueueDone] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
                + " job=" + std::to_string(job_id)
                + " job_progress=0/0 job_remaining=0 remaining=0"
                + " eval_limit=" + job_eval_limit
                + " game_ply_limit=" + job_game_ply_limit
                + " book_extend_ply=" + job_book_extend_ply);
        }
    }

    bookminer::BookStore& book_;
    std::vector<std::unique_ptr<bookminer::UsiEngine>>& engines_;
    TaskQueue queue_;
    std::vector<std::thread> threads_;
    std::atomic<int> next_job_id_{1};
    std::atomic<bool> stopped_{false};
    std::mutex progress_mutex_;
    std::map<int, JobProgress> jobs_;
    std::size_t total_enqueued_ = 0;
    std::size_t total_taken_ = 0;
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
    if (const auto* position = peta_book.find_position(key))
        return {position, false};

    if (const auto* position = peta_book.find_position(key.flipped()))
        return {position, true};

    return {};
}

BestMoveInfo peta_best_moveinfo_for_sfen(const bookminer::BookStore& peta_book, const std::string& sfen)
{
    const auto hit = find_peta_position_with_flip(peta_book, sfen);
    if (hit.position == nullptr || hit.position->moves.empty())
        return {};
    return best_moveinfo_in_sfen_orientation(*hit.position, hit.flipped);
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

struct PetaNextNode {
    std::string sfen_with_ply;
    int root_best_eval = 0;
    int eval_diff = 0;
};

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

private:
    std::vector<std::string> order_;
    std::unordered_map<std::string, PetaNextNode> values_;
};

std::vector<std::pair<std::string, std::string>> load_peta_next_root_positions(const std::filesystem::path& start_sfens_path)
{
    std::vector<std::pair<std::string, std::string>> roots;
    const bool has_start_sfens_file = fs::is_regular_file(start_sfens_path);

    if (has_start_sfens_file)
    {
        log_line("read start sfens , path = " + start_sfens_path.string());
        std::ifstream in(start_sfens_path);
        if (!in)
            throw std::runtime_error("failed to open peta_next start sfens file: " + start_sfens_path.string());

        std::string line;
        while (std::getline(in, line))
        {
            if (!line.empty() && line.back() == '\r')
                line.pop_back();
            auto first = std::find_if(line.begin(), line.end(), [](unsigned char ch) {
                return !std::isspace(ch);
            });
            if (first == line.end())
                continue;
            if (*first == '#')
                continue;

            const std::string position_command(first, line.end());
            const std::string sfen_with_ply = decode_position_string_to_sfen_with_ply(position_command);
            log_line("start sfen = " + sfen_with_ply);
            roots.emplace_back(position_command, sfen_with_ply);
        }
    }

    if (!has_start_sfens_file)
        roots.emplace_back("startpos", bookminer::StartSfenPly1);

    return roots;
}

std::vector<std::string> peta_next_for_turn(
    const bookminer::BookStore* source_book,
    const bookminer::BookStore& peta_book,
    int turn,
    int peta_eval_diff,
    int max_step,
    int max_book_ply,
    const std::filesystem::path& start_sfens_path,
    std::optional<int> eval_refutation_margin)
{
    std::vector<std::string> think_sfens;
    std::unordered_set<std::string> think_seen;

    const std::string turn_str = turn == 1 ? "black" : "white";
    const bool filter_refutation = source_book != nullptr && eval_refutation_margin.has_value();
    log_line(std::string("--- ") + (filter_refutation ? "peta_next_refutation " : "peta_next ") + turn_str + " ---");

    std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> visited;
    const auto root_positions = load_peta_next_root_positions(start_sfens_path);

    OrderedPetaNextPositions current_positions;
    for (const auto& [position_command, sfen_with_ply] : root_positions)
    {
        auto [sfen, ply] = bookminer::trim_sfen_ply(sfen_with_ply);
        if (ply >= max_book_ply)
            continue;

        const auto hit = find_peta_position_with_flip(peta_book, sfen);
        if (hit.position == nullptr || hit.position->moves.empty())
        {
            if (!filter_refutation)
                append_unique_position_command(think_sfens, think_seen, position_command);
            continue;
        }

        const auto root_best = get_best(*hit.position);
        if (!root_best.has_value())
        {
            if (!filter_refutation)
                append_unique_position_command(think_sfens, think_seen, position_command);
            continue;
        }

        current_positions.set(position_command, PetaNextNode{sfen_with_ply, root_best->first, peta_eval_diff});
        log_line("root sfen : " + sfen_with_ply + " , root_best = " + std::to_string(root_best->first));
    }

    int step = 1;
    while (!current_positions.empty())
    {
        if (step > max_step)
            break;

        OrderedPetaNextPositions next_positions;

        for (const auto& position_command : current_positions.order())
        {
            const auto& node = current_positions.at(position_command);
            auto [sfen, ply] = bookminer::trim_sfen_ply(node.sfen_with_ply);

            if (ply >= max_book_ply)
                continue;

            if (visited_peta_position(visited, sfen))
                continue;

            visited.insert(bookminer::PackedSfen::from_sfen(sfen));

            const auto hit = find_peta_position_with_flip(peta_book, sfen);
            if (hit.position == nullptr)
            {
                if (!filter_refutation)
                    append_unique_position_command(think_sfens, think_seen, position_command);
                continue;
            }

            const auto& moveinfos = hit.position->moves;
            if (moveinfos.empty())
                continue;

            const int best_eval = static_cast<int>(moveinfos.front().eval);
            const int eval_low = (ply % 2 == turn) ? best_eval : node.root_best_eval - node.eval_diff;

            for (std::size_t move_index = 0; move_index < moveinfos.size(); ++move_index)
            {
                const auto& moveinfo = moveinfos[move_index];
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

                if (filter_refutation)
                {
                    const auto next_hit = find_peta_position_with_flip(peta_book, next_sfen_ply.first);
                    if (next_hit.position == nullptr)
                    {
                        if (move_index == 0
                            && moveinfo.depth == 0
                            && is_refuted_move_by_peta_impact(
                                *source_book,
                                *hit.position,
                                hit.flipped,
                                sfen,
                                move16,
                                static_cast<int>(moveinfo.eval),
                                *eval_refutation_margin))
                        {
                            append_unique_position_command(think_sfens, think_seen, next_position_command);
                        }
                        continue;
                    }
                }

                next_positions.set(next_position_command, PetaNextNode{next_sfen_with_ply, -node.root_best_eval, node.eval_diff});
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

std::size_t write_position_commands_file(const std::filesystem::path& path, const std::vector<std::string>& position_commands)
{
    if (path.has_parent_path())
        fs::create_directories(path.parent_path());

    std::ofstream out(path);
    if (!out)
        throw std::runtime_error("failed to open output file: " + path.string());

    for (const auto& position_command : position_commands)
        out << position_command << '\n';

    return position_commands.size();
}

std::size_t write_position_command_entries_file(const std::filesystem::path& path, const std::vector<PositionCommandEntry>& position_commands)
{
    if (path.has_parent_path())
        fs::create_directories(path.parent_path());

    std::ofstream out(path);
    if (!out)
        throw std::runtime_error("failed to open output file: " + path.string());

    for (const auto& position_command : position_commands)
        out << format_position_command_entry(position_command) << '\n';

    return position_commands.size();
}

std::size_t merge_black_white_think_sfens(const std::filesystem::path& black_path, const std::filesystem::path& white_path, const std::filesystem::path& output_path)
{
    std::ifstream black(black_path);
    std::ifstream white(white_path);
    std::ofstream out(output_path);
    if (!black)
        throw std::runtime_error("failed to open file: " + black_path.string());
    if (!white)
        throw std::runtime_error("failed to open file: " + white_path.string());
    if (!out)
        throw std::runtime_error("failed to open output file: " + output_path.string());

    std::size_t count = 0;
    while (true)
    {
        std::string black_line;
        std::string white_line;
        const bool has_black = static_cast<bool>(std::getline(black, black_line));
        const bool has_white = static_cast<bool>(std::getline(white, white_line));
        if (!has_black && !has_white)
            break;

        if (has_black)
        {
            out << black_line << '\n';
            ++count;
        }
        if (has_white)
        {
            out << white_line << '\n';
            ++count;
        }
    }

    return count;
}

void peta_next(
    const bookminer::BookStore* source_book,
    const bookminer::BookStore& peta_book,
    int peta_eval_diff,
    int max_step,
    int max_book_ply,
    const std::filesystem::path& start_sfens_path,
    std::optional<int> eval_refutation_margin)
{
    const bool filter_refutation = source_book != nullptr && eval_refutation_margin.has_value();
    log_line(
        std::string(filter_refutation ? "peta_next_refutation" : "peta_next")
        + ", peta_eval_diff = " + std::to_string(peta_eval_diff)
        + ", max_step = " + std::to_string(max_step)
        + ", max_book_ply = " + std::to_string(max_book_ply)
        + ", start_sfens_path = " + start_sfens_path.string()
        + (filter_refutation ? ", eval_refutation_margin = " + std::to_string(*eval_refutation_margin) : ""));

    const auto black = peta_next_for_turn(source_book, peta_book, 1, peta_eval_diff, max_step, max_book_ply, start_sfens_path, eval_refutation_margin);
    const fs::path black_path = fs::path(BookDir) / "think_sfens-black.txt";
    log_line("write book path = " + black_path.string() + ", len(think_sfens) = " + std::to_string(black.size()) + ".");
    write_position_commands_file(black_path, black);

    const auto white = peta_next_for_turn(source_book, peta_book, 0, peta_eval_diff, max_step, max_book_ply, start_sfens_path, eval_refutation_margin);
    const fs::path white_path = fs::path(BookDir) / "think_sfens-white.txt";
    log_line("write book path = " + white_path.string() + ", len(think_sfens) = " + std::to_string(white.size()) + ".");
    write_position_commands_file(white_path, white);

    const fs::path output_path = fs::path(BookDir) / ThinkSfensName;
    const std::size_t count = merge_black_white_think_sfens(black_path, white_path, output_path);

    log_line(std::string(filter_refutation ? "peta_next_refutation" : "peta_next") + " done.");
    log_line(std::string("[") + (filter_refutation ? "PetaNextRefutationDone" : "PetaNextDone")
        + "] path=" + output_path.string() + " count=" + std::to_string(count));
}

std::optional<std::string> peta_pv_leaf_position_command(
    const bookminer::BookStore& peta_book,
    std::string position_command,
    const std::string& sfen_with_ply,
    std::uint16_t first_move16,
    int max_book_ply,
    int max_step = PetaDefaultMaxStep)
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
            return leaf_position_command;
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

void peta_depth_gap(
    const bookminer::BookStore& peta_book,
    double eval_per_ply,
    int max_book_ply)
{
    std::ostringstream eval_per_ply_stream;
    eval_per_ply_stream << eval_per_ply;
    log_line("peta_depth_gap, eval_per_ply = " + eval_per_ply_stream.str()
        + ", max_book_ply = " + std::to_string(max_book_ply));

    std::vector<std::string> think_sfens;
    std::unordered_set<std::string> think_seen;
    std::size_t candidates = 0;
    std::size_t skipped_by_ply = 0;
    std::size_t skipped_by_best_depth = 0;
    const auto peta_entries = peta_book.snapshot_entries();
    const std::size_t total = peta_entries.size();

    for (std::size_t index = 0; index < peta_entries.size(); ++index)
    {
        const std::size_t processed = index + 1;
        if (processed % PetaDepthGapProgressInterval == 0)
        {
            log_line("depth_gap progress nodes = " + std::to_string(processed) + "/" + std::to_string(total)
                + " , candidates = " + std::to_string(candidates)
                + " , think_sfens = " + std::to_string(think_sfens.size()));
        }

        const auto& entry = peta_entries[index];
        const auto& peta_position = entry.position;
        if (peta_position.moves.size() < 2)
            continue;

        const auto& best = peta_position.moves.front();
        if (best.depth >= PetaDepthGapMaxBestDepth)
        {
            ++skipped_by_best_depth;
            continue;
        }
        const std::string sfen = bookminer::trim_sfen(bookminer::unpack_sfen_bytes(entry.key.bytes));
        const std::string sfen_with_ply = sfen + " " + std::to_string(peta_position.ply);
        if (bookminer::trim_sfen_ply(sfen_with_ply).second >= max_book_ply)
            continue;

        const std::string position_command = "sfen " + sfen_with_ply;
        for (std::size_t move_index = 1; move_index < peta_position.moves.size(); ++move_index)
        {
            const auto& candidate = peta_position.moves[move_index];
            const int depth_gap = best.depth - candidate.depth;
            if (depth_gap <= 0)
                continue;
            if (static_cast<double>(candidate.eval) + depth_gap * eval_per_ply < static_cast<double>(best.eval))
                continue;

            ++candidates;
            const auto leaf_position_command = peta_pv_leaf_position_command(
                peta_book,
                position_command,
                sfen_with_ply,
                candidate.move16,
                max_book_ply);
            if (!leaf_position_command.has_value())
            {
                ++skipped_by_ply;
                continue;
            }
            append_unique_position_command(think_sfens, think_seen, *leaf_position_command);
        }
    }

    const fs::path output_path = fs::path(BookDir) / ThinkSfensName;
    log_line("write book path = " + output_path.string() + ", len(think_sfens) = " + std::to_string(think_sfens.size()) + ".");
    write_position_commands_file(output_path, think_sfens);

    log_line("peta_depth_gap done.");
    log_line("[PetaDepthGapDone] path=" + output_path.string()
        + " count=" + std::to_string(think_sfens.size())
        + " candidates=" + std::to_string(candidates)
        + " skipped_by_ply=" + std::to_string(skipped_by_ply)
        + " skipped_by_best_depth=" + std::to_string(skipped_by_best_depth));
}

void peta_unsolved(
    const bookminer::BookStore& peta_book,
    int eval_diff,
    int max_book_ply,
    int max_step)
{
    const fs::path input_path = fs::path(BookDir) / ThinkUnsolvedSfensName;
    log_line("peta_unsolved, eval_diff = " + std::to_string(eval_diff)
        + ", max_book_ply = " + std::to_string(max_book_ply)
        + ", max_step = " + std::to_string(max_step)
        + ", unsolved_sfens_path = " + input_path.string());

    std::ifstream input(input_path);
    if (!input)
        throw std::runtime_error("think_unsolved_sfens file not found : " + input_path.string());

    std::vector<std::string> think_sfens;
    std::unordered_set<std::string> think_seen;
    std::size_t input_games = 0;
    std::size_t processed_prefixes = 0;
    std::size_t candidates = 0;
    std::size_t skipped_by_eval_diff = 0;
    std::size_t skipped_by_ply = 0;
    std::size_t skipped_no_peta = 0;
    std::size_t skipped_no_leaf = 0;

    std::string raw_line;
    while (std::getline(input, raw_line))
    {
        if (!raw_line.empty() && raw_line.back() == '\r')
            raw_line.pop_back();
        const std::string position_line = trim_copy(raw_line);
        if (position_line.empty() || position_line.front() == '#')
            continue;

        ++input_games;
        const auto prefixes = position_prefixes(position_line);
        if (prefixes.empty())
            continue;

        const auto [root_sfen, root_ply] = bookminer::trim_sfen_ply(prefixes.front().second);
        const auto root_best = peta_best_moveinfo_for_sfen(peta_book, root_sfen);
        if (root_best.info == nullptr)
        {
            ++skipped_no_peta;
            continue;
        }
        const int root_eval = static_cast<int>(root_best.info->eval);

        for (const auto& [prefix_command, prefix_sfen_with_ply] : prefixes)
        {
            ++processed_prefixes;
            if (processed_prefixes % PetaUnsolvedProgressInterval == 0)
            {
                log_line("unsolved progress prefixes = " + std::to_string(processed_prefixes)
                    + " , think_sfens = " + std::to_string(think_sfens.size())
                    + " , skipped_by_eval_diff = " + std::to_string(skipped_by_eval_diff)
                    + " , skipped_by_ply = " + std::to_string(skipped_by_ply)
                    + " , skipped_no_peta = " + std::to_string(skipped_no_peta));
            }

            const auto [prefix_sfen, prefix_ply] = bookminer::trim_sfen_ply(prefix_sfen_with_ply);
            if (prefix_ply >= max_book_ply)
            {
                ++skipped_by_ply;
                continue;
            }

            const auto best = peta_best_moveinfo_for_sfen(peta_book, prefix_sfen);
            if (best.info == nullptr)
            {
                ++skipped_no_peta;
                continue;
            }

            const int best_eval = static_cast<int>(best.info->eval);
            const int current_eval_from_root = ((prefix_ply - root_ply) % 2 == 0) ? best_eval : -best_eval;
            if (root_eval - current_eval_from_root >= eval_diff)
            {
                ++skipped_by_eval_diff;
                continue;
            }

            ++candidates;
            const auto leaf_position_command = peta_pv_leaf_position_command(
                peta_book,
                prefix_command,
                prefix_sfen_with_ply,
                best.oriented_move16,
                max_book_ply,
                max_step);
            if (!leaf_position_command.has_value())
            {
                ++skipped_no_leaf;
                continue;
            }
            append_unique_position_command(think_sfens, think_seen, *leaf_position_command);
        }
    }

    const fs::path output_path = fs::path(BookDir) / ThinkSfensName;
    log_line("write book path = " + output_path.string() + ", len(think_sfens) = " + std::to_string(think_sfens.size()) + ".");
    write_position_commands_file(output_path, think_sfens);

    log_line("peta_unsolved done.");
    log_line("[PetaUnsolvedDone] path=" + output_path.string()
        + " count=" + std::to_string(think_sfens.size())
        + " input_games=" + std::to_string(input_games)
        + " processed_prefixes=" + std::to_string(processed_prefixes)
        + " candidates=" + std::to_string(candidates)
        + " skipped_by_eval_diff=" + std::to_string(skipped_by_eval_diff)
        + " skipped_by_ply=" + std::to_string(skipped_by_ply)
        + " skipped_no_peta=" + std::to_string(skipped_no_peta)
        + " skipped_no_leaf=" + std::to_string(skipped_no_leaf));
}

void peta_refutation(
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
        if (eval_limit.has_value() && std::abs(static_cast<int>(old_candidate->eval)) > *eval_limit)
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
    log_line("[PetaRefutationDone] path=" + output_path.string()
        + " count=" + std::to_string(think_sfens.size())
        + " skipped_by_eval_limit=" + std::to_string(skipped_by_eval_limit)
        + " skipped_by_ply=" + std::to_string(skipped_by_ply));
}

bool is_supported_opponent_book_path(const fs::path& path)
{
    return path.extension() == ".db" || bookminer::is_yane_bin_book_path(path);
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
    const int eval_low = best_eval - eval_diff;
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
    std::mt19937_64& rng)
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

void peta_opponent(
    const bookminer::BookStore& peta_book,
    int eval_diff,
    int max_book_ply,
    int max_step,
    std::optional<int> book_extend_ply)
{
    log_line("peta_opponent, eval_diff = " + std::to_string(eval_diff)
        + ", max_book_ply = " + std::to_string(max_book_ply)
        + ", max_step = " + std::to_string(max_step)
        + ", book_extend_ply = " + (book_extend_ply.has_value() ? std::to_string(*book_extend_ply) : std::string("None"))
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
                        rng);
                    if (leaf.has_value())
                    {
                        add_position_command_entry(think_sfens, think_indexes, *leaf, book_extend_ply);
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
    log_line("[PetaOpponentDone] path=" + output_path.string()
        + " count=" + std::to_string(think_sfens.size())
        + " opponent_books=" + std::to_string(opponent_paths.size())
        + " processed_nodes=" + std::to_string(processed_nodes)
        + " cut_nodes=" + std::to_string(cut_nodes)
        + " leaf_nodes=" + std::to_string(leaf_nodes)
        + " skipped_by_ply=" + std::to_string(skipped_by_ply)
        + " retired_repetition=" + std::to_string(retired_repetition)
        + " retired_max_step=" + std::to_string(retired_max_step));
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
        const bool is_text_book = entry.path().extension() == ".db";
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
        const bool is_text_book = entry.path().extension() == ".db";
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
    return path.extension() == ".db" || bookminer::is_yane_bin_book_path(path);
}

void ensure_supported_peta_book_path(const fs::path& path, const std::string& label)
{
    if (!is_supported_peta_book_path(path))
        throw std::runtime_error(label + " must be .db or .ybb : " + path.string());
}

fs::path make_backup_path(std::size_t position_count, std::optional<int> ply_limit)
{
    fs::create_directories(BookBackupDir);
    std::string filename = std::string(BookDbName) + "-" + make_time_stamp() + "_" + std::to_string(position_count);
    if (ply_limit.has_value())
        filename += "_ply" + std::to_string(*ply_limit);
    filename += ".ybb";
    return fs::path(BookBackupDir) / filename;
}

std::optional<std::pair<std::string, std::size_t>> parse_regular_book_backup_name(const fs::path& path)
{
    const std::string filename = path.filename().string();
    const std::regex pattern("^" + std::string(BookDbName) + R"(-(\d{14})_(\d+)(?:\.db|\.ybb)$)");
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

fs::path find_peta_shock_engine(const fs::path& app_dir)
{
    const fs::path engine_path = app_dir.parent_path() / "BookMiner" / PetaShockEngineName;
    if (fs::is_regular_file(engine_path))
        return engine_path;
    throw std::runtime_error("peta shock engine not found : " + engine_path.string());
}

void emit_prefixed_output_line(std::string line)
{
    if (!line.empty() && line.back() == '\r')
        line.pop_back();
    if (!line.empty() && !is_yaneuraou_progress_bar_line(line))
        log_line("[peta_shock] " + line);
}

void emit_output_chunk(std::string& pending, const char* data, std::size_t size)
{
    pending.append(data, size);
    std::size_t pos = 0;
    while ((pos = pending.find('\n')) != std::string::npos)
    {
        std::string line = pending.substr(0, pos);
        pending.erase(0, pos + 1);
        emit_prefixed_output_line(std::move(line));
    }
}

#ifdef _WIN32
std::string quote_windows_arg(const fs::path& path)
{
    std::string s = path.string();
    std::string quoted = "\"";
    for (char ch : s)
    {
        if (ch == '"')
            quoted += "\\\"";
        else
            quoted += ch;
    }
    quoted += "\"";
    return quoted;
}

int run_process_with_input(const fs::path& executable, const fs::path& cwd, const std::string& input)
{
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
        throw std::runtime_error("CreatePipe stdout failed");

    SetHandleInformation(child_stdin_write, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(child_stdout_read, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOA si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = child_stdin_read;
    si.hStdOutput = child_stdout_write;
    si.hStdError = child_stdout_write;

    PROCESS_INFORMATION pi{};
    std::string command_line = quote_windows_arg(executable);
    std::string cwd_string = cwd.string();

    if (!CreateProcessA(nullptr, command_line.data(), nullptr, nullptr, TRUE, 0, nullptr, cwd_string.c_str(), &si, &pi))
        throw std::runtime_error("CreateProcess failed: " + executable.string());

    CloseHandle(child_stdin_read);
    CloseHandle(child_stdout_write);

    DWORD written = 0;
    if (!input.empty())
        WriteFile(child_stdin_write, input.data(), static_cast<DWORD>(input.size()), &written, nullptr);
    CloseHandle(child_stdin_write);

    std::string pending;
    char buffer[4096];
    DWORD read_size = 0;
    while (ReadFile(child_stdout_read, buffer, sizeof(buffer), &read_size, nullptr) && read_size > 0)
        emit_output_chunk(pending, buffer, read_size);
    if (!pending.empty())
        emit_prefixed_output_line(std::move(pending));
    CloseHandle(child_stdout_read);

    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD exit_code = 1;
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return static_cast<int>(exit_code);
}
#else
void set_nonblocking(int fd)
{
    const int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0)
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

int run_process_with_input(const fs::path& executable, const fs::path& cwd, const std::string& input)
{
    constexpr int PetaShockProgressIntervalSeconds = 10;

    int stdin_pipe[2]{};
    int stdout_pipe[2]{};
    if (pipe(stdin_pipe) != 0 || pipe(stdout_pipe) != 0)
        throw std::runtime_error("pipe failed");

    const pid_t pid = fork();
    if (pid < 0)
        throw std::runtime_error("fork failed");

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

    const ssize_t written = write(stdin_pipe[1], input.data(), input.size());
    (void)written;
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
                    emit_output_chunk(pending, buffer, static_cast<std::size_t>(n));
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
        emit_prefixed_output_line(std::move(pending));

    if (!child_done)
        waitpid(pid, &status, 0);

    if (WIFEXITED(status))
        return WEXITSTATUS(status);
    return 1;
}
#endif

fs::path run_peta_shock_makebook(const fs::path& app_dir, const fs::path& source_book_path)
{
    const fs::path engine_path = find_peta_shock_engine(app_dir);
    const std::string source_book_rel = to_book_dir_relative_path(source_book_path);
    const fs::path peta_path = peta_book_backup_path_from_source(source_book_path);
    const fs::path peta_temp_path = bookminer::temp_book_path(peta_path);
    const std::string peta_temp_rel = to_book_dir_relative_path(peta_temp_path);

    fs::create_directories(BookBackupDir);
    std::error_code ec;
    fs::remove(peta_temp_path, ec);

    const std::string makebook_command = "makebook peta_shock " + source_book_rel + " " + peta_temp_rel;

    log_line("start peta_shock makebook");
    log_line("engine path = " + engine_path.string());
    log_line("source book = " + source_book_path.string());
    log_line("peta book   = " + peta_path.string());
    log_line("command     = " + makebook_command);

    const std::string commands =
        "setoption name BookDir value book\n"
        "setoption name BookFile value no_book\n"
        "setoption name FlippedBook value true\n"
        "setoption name USI_Hash value 1\n" +
        makebook_command + "\n"
        "quit\n";

    const int exit_code = run_process_with_input(engine_path, app_dir, commands);
    if (exit_code != 0)
        throw std::runtime_error("peta_shock makebook failed. return code = " + std::to_string(exit_code));

    if (!fs::is_regular_file(peta_temp_path) || fs::file_size(peta_temp_path) == 0)
        throw std::runtime_error("peta_shock makebook failed. output file was not created : " + peta_temp_path.string());

    fs::remove(peta_path, ec);
    fs::rename(peta_temp_path, peta_path);
    log_line("..peta_shock makebook has done, path = " + peta_path.string());
    return peta_path;
}

fs::path save_book_backup(const bookminer::BookStore& book, std::optional<int> ply_limit)
{
    std::scoped_lock save_lock(g_save_mutex);
    const auto count = book.count_save_positions(ply_limit);
    const auto path = make_backup_path(count, ply_limit);
    log_line("start save_book_backup , path = " + path.string());
    book.save_yaneuraou_book(path, ply_limit, book_write_progress, nullptr);
    log_line("..save_book_backup has done, " + std::to_string(count) + " positions.");
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
        if (started_)
            return;
        started_ = true;
        const auto next = std::chrono::system_clock::now() + std::chrono::seconds(interval_seconds_);
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

private:
    void run()
    {
        while (true)
        {
            const auto next = std::chrono::system_clock::now() + std::chrono::seconds(interval_seconds_);
            log_line("[BackupNext] next=" + scheduled_time_text(next)
                + " interval=" + std::to_string(interval_seconds_));

            std::unique_lock lock(mutex_);
            if (cv_.wait_until(lock, next, [&] { return stopping_; }))
                return;
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
        }
    }

    const bookminer::BookStore& book_;
    int interval_seconds_ = 0;
    std::thread thread_;
    std::mutex mutex_;
    std::condition_variable cv_;
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

void read_peta_book(bookminer::BookStore& peta_book, const std::optional<std::string>& peta_book_path)
{
    const fs::path peta_path = resolve_peta_book_path(peta_book_path);

    log_line("read peta shocked book , path = " + peta_path.string());
    peta_book.load_yaneuraou_book(peta_path, false, book_read_progress, nullptr);
    log_line("reading the peta_book has done.");
}

void make_and_read_peta_book(
    const fs::path& app_dir,
    bookminer::BookStore& peta_book,
    const std::optional<std::string>& source_book_path)
{
    const fs::path source = resolve_peta_source_book_path(source_book_path);
    const fs::path peta_path = run_peta_shock_makebook(app_dir, source);
    read_peta_book(peta_book, std::optional<std::string>{peta_path.string()});
}

void write_and_read_peta_book(const fs::path& app_dir, bookminer::BookStore& peta_book, const fs::path& source_book_path)
{
    log_line("start p command : write backup, peta_shock, and read peta book.");
    log_line("p command source book = " + source_book_path.string());
    make_and_read_peta_book(app_dir, peta_book, std::optional<std::string>{source_book_path.string()});
    log_line("..p command has done.");
    log_line("[PetaCommandDone]");
}

void print_help()
{
    log_line("Help : ");
    log_line("  Q : quit");
    log_line("  ! : quit without saving");
    log_line("  W : write book backup        , w (ply_limit)");
    log_line("  T : think positions          , t (eval_limit) (max_book_ply) (think_command_ply)");
    log_line("  R : read peta shocked book , r (peta book path)");
    log_line("  P : write backup, make and read peta shocked book");
    log_line("  PN : peta_shock next         , pn peta_eval_diff (max_book_ply) (max_step)");
    log_line("  PNF: peta next refutation   , pnf peta_eval_diff (max_book_ply) (max_step) (eval_refutation_margin)");
    log_line("  PF : peta refutation         , pf (eval_refutation_margin) (eval_limit) (max_book_ply)");
    log_line("  PD : peta depth gap          , pd (eval_per_ply) (max_book_ply)");
    log_line("  PU : peta unsolved           , pu (eval_diff) (max_book_ply) (max_step)");
    log_line("  PO : peta opponent           , po (eval_diff) (max_book_ply) (max_step) (book_extend_ply)");
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
    const fs::path app_dir = executable_dir(argv[0]);
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
    std::vector<std::unique_ptr<bookminer::UsiEngine>> engines;
    std::unique_ptr<TaskWorkers> task_workers;
    std::unique_ptr<AutoSaveService> auto_save_service;
    int max_book_ply = 200;
    std::optional<fs::path> clean_source_path;
    std::uint64_t clean_source_revision = 0;

    try
    {
        const auto book_miner_settings = bookminer::load_book_miner_settings({
            BookMinerSettingsPath,
            BookMinerCppSettingsPath,
        });
        log_line(
            "BookMiner settings : auto_save_interval_seconds = " + std::to_string(book_miner_settings.auto_save_interval_seconds)
            + ", max_book_ply = " + std::to_string(book_miner_settings.max_book_ply)
            + ", peta_next_start_sfens_path = " + book_miner_settings.peta_next_start_sfens_path);
        max_book_ply = book_miner_settings.max_book_ply;

        log_line("[StartupStage] stage=book_read message=定跡DBを読み込み中");
        clean_source_path = load_latest_book_backup(book);
        clean_source_revision = book.revision();
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

        std::string line;
        while (true)
        {
            if (!from_gui)
                logger.prompt();
            if (!std::getline(std::cin, line))
                break;

            auto tokens = split_ws(line);
            if (tokens.empty())
                continue;

            std::string command = tokens[0];
            std::transform(command.begin(), command.end(), command.begin(), [](unsigned char ch) {
                return static_cast<char>(std::tolower(ch));
            });

            if (command == "h")
            {
                print_help();
            }
            else if (command == "q")
            {
                log_line("quit");
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
                break;
            }
            else if (command == "!")
            {
                log_line("quit without saving");
                if (auto_save_service)
                    auto_save_service->stop();
                if (task_workers)
                    task_workers->stop(true);
                break;
            }
            else if (command == "w")
            {
                std::optional<int> ply_limit;
                if (tokens.size() >= 2)
                    ply_limit = std::stoi(tokens[1]);
                const auto before_revision = book.revision();
                const auto path = save_book_backup(book, ply_limit);
                if (!ply_limit.has_value() && book.revision() == before_revision)
                {
                    clean_source_path = path;
                    clean_source_revision = before_revision;
                }
                log_line("write path = " + path.string());
                log_line("..w command write has done. path = " + path.string());
            }
            else if (command == "t")
            {
                const std::string path = fs::path(BookDir).append(ThinkSfensName).string();
                const int task_eval_limit = parse_int_argument(tokens, 1, DefaultEvalLimit);
                const int task_max_book_ply = parse_int_argument(tokens, 2, max_book_ply);
                const int task_think_command_ply = parse_int_argument(tokens, 3, ThinkCommandPly);
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
                fs::path source_book_path;
                const auto current_revision = book.revision();
                if (clean_source_path.has_value()
                    && fs::is_regular_file(*clean_source_path)
                    && clean_source_revision == current_revision)
                {
                    source_book_path = *clean_source_path;
                    log_line("p command source book reused = " + source_book_path.string());
                }
                else
                {
                    const auto before_revision = book.revision();
                    source_book_path = save_book_backup(book, std::nullopt);
                    if (book.revision() == before_revision)
                    {
                        clean_source_path = source_book_path;
                        clean_source_revision = before_revision;
                    }
                }
                write_and_read_peta_book(app_dir, peta_book, source_book_path);
            }
            else if (command == "pn")
            {
                if (tokens.size() < 2)
                {
                    log_line("Usage : pn peta_eval_diff (max_book_ply) (max_step)");
                }
                else
                {
                    const int peta_eval_diff = parse_int_argument(tokens, 1, PetaDefaultInfEvalDiff);
                    const int command_max_book_ply = parse_int_argument(tokens, 2, max_book_ply);
                    const int max_step = parse_int_argument(tokens, 3, PetaDefaultMaxStep);
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
                    peta_next(
                        nullptr,
                        peta_book,
                        peta_eval_diff,
                        max_step,
                        command_max_book_ply,
                        book_miner_settings.peta_next_start_sfens_path,
                        std::nullopt);
                }
            }
            else if (command == "pnf")
            {
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
                    peta_next(
                        &book,
                        peta_book,
                        peta_eval_diff,
                        max_step,
                        command_max_book_ply,
                        book_miner_settings.peta_next_start_sfens_path,
                        eval_refutation_margin);
                }
            }
            else if (command == "pf")
            {
                const int eval_refutation_margin = parse_int_argument(tokens, 1, DefaultEvalRefutationMargin);
                std::optional<int> refutation_eval_limit = parse_optional_int_argument(tokens, 2);
                const int command_max_book_ply = parse_int_argument(tokens, 3, max_book_ply);
                if (command_max_book_ply <= 0)
                {
                    log_line("Error : max_book_ply must be positive integer.");
                    continue;
                }
                peta_refutation(
                    book,
                    peta_book,
                    eval_refutation_margin,
                    refutation_eval_limit,
                    command_max_book_ply);
            }
            else if (command == "pd")
            {
                const double eval_per_ply = parse_double_argument(tokens, 1, DefaultDepthGapEvalPerPly);
                const int command_max_book_ply = parse_int_argument(tokens, 2, max_book_ply);
                if (eval_per_ply < 0)
                {
                    log_line("Error : eval_per_ply must be non-negative number.");
                    continue;
                }
                if (command_max_book_ply <= 0)
                {
                    log_line("Error : max_book_ply must be positive integer.");
                    continue;
                }
                peta_depth_gap(
                    peta_book,
                    eval_per_ply,
                    command_max_book_ply);
            }
            else if (command == "pu")
            {
                const int eval_diff = parse_int_argument(tokens, 1, PetaDefaultInfEvalDiff);
                const int command_max_book_ply = parse_int_argument(tokens, 2, max_book_ply);
                const int max_step = parse_int_argument(tokens, 3, PetaDefaultMaxStep);
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
                peta_unsolved(
                    peta_book,
                    eval_diff,
                    command_max_book_ply,
                    max_step);
            }
            else if (command == "po")
            {
                const int eval_diff = parse_int_argument(tokens, 1, PetaOpponentDefaultEvalDiff);
                const int command_max_book_ply = parse_int_argument(tokens, 2, max_book_ply);
                const int max_step = parse_int_argument(tokens, 3, PetaDefaultMaxStep);
                const std::optional<int> book_extend_ply = parse_optional_int_argument(tokens, 4);
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
                if (book_extend_ply.has_value() && *book_extend_ply < 0)
                {
                    log_line("Error : book_extend_ply must be non-negative integer or None.");
                    continue;
                }
                peta_opponent(
                    peta_book,
                    eval_diff,
                    command_max_book_ply,
                    max_step,
                    book_extend_ply);
            }
            else if (command == "r")
            {
                std::optional<std::string> peta_book_path;
                if (tokens.size() >= 2)
                    peta_book_path = tokens[1];
                read_peta_book(peta_book, peta_book_path);
                log_line("[PetaReadDone]");
            }
            else
            {
                log_line("unknown command: " + command);
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
