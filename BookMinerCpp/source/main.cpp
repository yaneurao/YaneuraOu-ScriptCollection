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
constexpr const char* EngineSettingsPath = "settings/engine_settings.json5";
constexpr const char* BookMinerSettingsPath = "settings/book_miner_settings.json5";
constexpr const char* BookMinerCppSettingsPath = "settings/book_miner_cpp_settings.json5";
constexpr int PetaShockProgressIntervalSeconds = 10;
constexpr int ThinkCommandPly = 6;
constexpr int PlyMin = std::numeric_limits<int>::min();

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
        moves.push_back(bookminer::MoveInfo{move16, static_cast<std::int16_t>(move.eval)});
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
    int max_book_ply)
{
    std::string current_sfen = leaf_sfen;
    int current_ply = leaf_ply;
    int rest_ply = ThinkCommandPly;
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
    int max_book_ply)
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

    start_thinking_best_line(book, engine, board.sfen(), board.ply(), eval_limit, max_book_ply);
}

std::vector<std::string> read_position_commands_file(const std::filesystem::path& path)
{
    std::ifstream in(path);
    if (!in)
        throw std::runtime_error("failed to open think_sfens file: " + path.string());

    std::vector<std::string> commands;
    std::string line;
    while (std::getline(in, line))
    {
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (line.empty() || line[0] == '#')
            continue;
        commands.push_back(line);
    }
    return commands;
}

struct Task {
    std::string position_command;
    int eval_limit = 0;
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
        int max_book_ply)
        : book_(book)
        , engines_(engines)
        , max_book_ply_(max_book_ply)
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

    int enqueue_position_commands(const std::filesystem::path& path, int eval_limit)
    {
        const auto commands = read_position_commands_file(path);
        const int job_id = next_job_id_.fetch_add(1);
        const auto added = commands.size();

        std::size_t total_taken = 0;
        std::size_t total_enqueued = 0;
        {
            std::scoped_lock lock(progress_mutex_);
            jobs_[job_id] = JobProgress{added, 0};
            total_enqueued_ += added;
            total_taken = total_taken_;
            total_enqueued = total_enqueued_;
        }

        log_line("(" + std::to_string(job_id) + ") put position commands , path = " + path.string()
            + " , eval_limit = " + std::to_string(eval_limit));
        log_line("(" + std::to_string(job_id) + ") read " + std::to_string(added) + " position commands.");
        log_line("[TaskQueueStart] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
            + " job=" + std::to_string(job_id)
            + " job_progress=0/" + std::to_string(added)
            + " job_remaining=" + std::to_string(added)
            + " added=" + std::to_string(added)
            + " remaining=" + std::to_string(total_enqueued - total_taken)
            + " path=" + path.string()
            + " eval_limit=" + std::to_string(eval_limit));

        for (const auto& command : commands)
            queue_.push(Task{command, eval_limit, job_id});

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
                process_position_command(book_, engine, task->position_command, task->eval_limit, max_book_ply_);
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

        const auto now = std::chrono::steady_clock::now();
        {
            std::scoped_lock lock(progress_mutex_);
            ++total_taken_;
            total_taken = total_taken_;
            total_enqueued = total_enqueued_;

            auto& job = jobs_[task.job_id];
            ++job.taken;
            job_taken = job.taken;
            job_total = job.total;

            const auto remaining = total_enqueued > total_taken ? total_enqueued - total_taken : 0;
            if (last_task_progress_report_.time_since_epoch().count() == 0
                || now - last_task_progress_report_ >= std::chrono::seconds(10)
                || remaining == 0
                || job_taken == job_total)
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
            + " remaining=" + std::to_string(remaining));

        if (remaining == 0)
        {
            log_line("[TaskQueueDone] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
                + " job=" + std::to_string(task.job_id)
                + " job_progress=" + std::to_string(job_taken) + "/" + std::to_string(job_total)
                + " job_remaining=" + std::to_string(job_remaining)
                + " remaining=0");
        }
    }

    void report_task_queue_done(int job_id)
    {
        std::size_t total_taken = 0;
        std::size_t total_enqueued = 0;
        {
            std::scoped_lock lock(progress_mutex_);
            total_taken = total_taken_;
            total_enqueued = total_enqueued_;
        }
        log_line("[TaskQueueDone] " + std::to_string(total_taken) + "/" + std::to_string(total_enqueued)
            + " job=" + std::to_string(job_id)
            + " job_progress=0/0 job_remaining=0 remaining=" + std::to_string(total_enqueued - total_taken));
    }

    bookminer::BookStore& book_;
    std::vector<std::unique_ptr<bookminer::UsiEngine>>& engines_;
    int max_book_ply_ = 0;
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

std::string decode_position_string_to_sfen_with_ply(const std::string& position_command)
{
    const auto parsed = bookminer::parse_position_command(position_command);
    auto board = bookminer::SfenPosition::from_sfen(parsed.start_sfen_with_ply);
    for (const auto& move : parsed.moves)
        board.push_usi(move);
    return board.sfen_with_ply();
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
    const bookminer::BookStore& peta_book,
    int turn,
    int peta_eval_diff,
    int max_step,
    int max_book_ply,
    const std::filesystem::path& start_sfens_path)
{
    std::vector<std::string> think_sfens;
    std::unordered_set<std::string> think_seen;

    const std::string turn_str = turn == 1 ? "black" : "white";
    log_line("--- peta_next " + turn_str + " ---");

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
            append_unique_position_command(think_sfens, think_seen, position_command);
            continue;
        }

        const auto root_best = get_best(*hit.position);
        if (!root_best.has_value())
        {
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
                append_unique_position_command(think_sfens, think_seen, position_command);
                continue;
            }

            const auto& moveinfos = hit.position->moves;
            if (moveinfos.empty())
                continue;

            const int best_eval = static_cast<int>(moveinfos.front().eval);
            const int eval_low = (ply % 2 == turn) ? best_eval : node.root_best_eval - node.eval_diff;

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
    const bookminer::BookStore& peta_book,
    int peta_eval_diff,
    int max_step,
    int max_book_ply,
    const std::filesystem::path& start_sfens_path)
{
    log_line(
        "peta_next, peta_eval_diff = " + std::to_string(peta_eval_diff)
        + ", max_step = " + std::to_string(max_step)
        + ", max_book_ply = " + std::to_string(max_book_ply)
        + ", start_sfens_path = " + start_sfens_path.string());

    const auto black = peta_next_for_turn(peta_book, 1, peta_eval_diff, max_step, max_book_ply, start_sfens_path);
    const fs::path black_path = fs::path(BookDir) / "think_sfens-black.txt";
    log_line("write book path = " + black_path.string() + ", len(think_sfens) = " + std::to_string(black.size()) + ".");
    write_position_commands_file(black_path, black);

    const auto white = peta_next_for_turn(peta_book, 0, peta_eval_diff, max_step, max_book_ply, start_sfens_path);
    const fs::path white_path = fs::path(BookDir) / "think_sfens-white.txt";
    log_line("write book path = " + white_path.string() + ", len(think_sfens) = " + std::to_string(white.size()) + ".");
    write_position_commands_file(white_path, white);

    const fs::path output_path = fs::path(BookDir) / ThinkSfensName;
    const std::size_t count = merge_black_white_think_sfens(black_path, white_path, output_path);

    log_line("peta_next done.");
    log_line("[PetaNextDone] path=" + output_path.string() + " count=" + std::to_string(count));
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
        if (entry.path().extension() != ".db")
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
        throw std::runtime_error(std::string("peta book file not found : ") + BookBackupDir + "/" + PetaBookDbName + "-*.db");
    return paths.back();
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
    const auto parsed = parse_regular_book_backup_name(source_book_path);
    if (!parsed.has_value())
        return fs::path(BookBackupDir) / (std::string(PetaBookDbName) + "-" + make_time_stamp() + ".db");

    const auto& [timestamp, position_count] = *parsed;
    return fs::path(BookBackupDir) / (std::string(PetaBookDbName) + "-" + timestamp + "_" + std::to_string(position_count) + ".db");
}

fs::path resolve_peta_source_book_path(const std::optional<std::string>& path)
{
    if (!path.has_value())
    {
        auto latest = get_latest_book_backup_or_none();
        if (!latest.has_value())
            throw std::runtime_error(std::string("book backup file not found : ") + BookBackupDir + "/" + BookDbName + "-*.db or " + BookDbName + "-*.ybb");
        return *latest;
    }

    const std::vector<fs::path> candidates = {
        fs::path(*path),
        fs::path(BookDir) / *path,
    };

    for (const auto& candidate : candidates)
        if (fs::is_regular_file(candidate))
            return candidate;

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
            return candidate;

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
    const std::vector<fs::path> candidates = {
        app_dir / PetaShockEngineName,
        app_dir.parent_path() / "BookMiner" / PetaShockEngineName,
    };
    for (const auto& candidate : candidates)
        if (fs::is_regular_file(candidate))
            return candidate;
    throw std::runtime_error("peta shock engine not found : " + (app_dir / PetaShockEngineName).string());
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

void load_latest_book_backup(bookminer::BookStore& book)
{
    auto path = get_latest_book_backup_or_none();
    if (!path.has_value())
    {
        log_line("book backup file not found. start with empty book. dir = " + std::string(BookBackupDir));
        return;
    }

    log_line("start load_book , path = " + path->string() + ", fast = True");
    book.load_yaneuraou_book(*path, false, book_read_progress, nullptr);
    log_line("done.." + std::to_string(book.size()) + " positions.");
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

void write_and_read_peta_book(const fs::path& app_dir, const bookminer::BookStore& book, bookminer::BookStore& peta_book)
{
    log_line("start p command : write backup, peta_shock, and read peta book.");
    const auto source_book_path = save_book_backup(book, std::nullopt);
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
    log_line("  T : think positions          , t (think_sfens path)");
    log_line("  E : EvalLimit                , e [eval_limit]");
    log_line("  R : read peta shocked book , r (peta book path)");
    log_line("  P : write backup, make and read peta shocked book");
    log_line("  N : peta_shock next          , n peta_eval_diff (max_step)");
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
    int eval_limit = 400;

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

        log_line("[StartupStage] stage=book_read message=定跡DBを読み込み中");
        load_latest_book_backup(book);
        log_line("[StartupStage] stage=book_read_done message=定跡DB読み込み完了");

        log_line("[StartupStage] stage=engine_init message=エンジン起動中");
        engines = bookminer::initialize_engines(load_engine_settings_with_log(), app_dir, [](const std::string& message) {
            log_line(message);
        });
        log_line("[StartupStage] stage=engine_init_done message=エンジン起動完了");

        log_line("[StartupStage] stage=task_worker message=探索worker起動中");
        task_workers = std::make_unique<TaskWorkers>(book, engines, book_miner_settings.max_book_ply);
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
                save_book_backup(book, std::nullopt);
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
                const auto path = save_book_backup(book, ply_limit);
                log_line("write path = " + path.string());
                log_line("..w command write has done. path = " + path.string());
            }
            else if (command == "e")
            {
                if (tokens.size() < 2)
                    log_line("Error : EvalLimit e");
                else
                {
                    eval_limit = std::stoi(tokens[1]);
                    log_line("eval_limit = " + std::to_string(eval_limit));
                }
            }
            else if (command == "t")
            {
                const std::string path = tokens.size() >= 2 ? tokens[1] : fs::path(BookDir).append(ThinkSfensName).string();
                if (!task_workers)
                    log_line("Error : task workers are not running.");
                else
                    task_workers->enqueue_position_commands(path, eval_limit);
            }
            else if (command == "p")
            {
                write_and_read_peta_book(app_dir, book, peta_book);
            }
            else if (command == "n")
            {
                if (tokens.size() < 2)
                {
                    log_line("Usage : n peta_eval_diff (max_step)");
                }
                else
                {
                    const int peta_eval_diff = std::stoi(tokens[1]);
                    const int max_step = tokens.size() >= 3 ? std::stoi(tokens[2]) : 9999;
                    peta_next(
                        peta_book,
                        peta_eval_diff,
                        max_step,
                        book_miner_settings.max_book_ply,
                        book_miner_settings.peta_next_start_sfens_path);
                }
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
