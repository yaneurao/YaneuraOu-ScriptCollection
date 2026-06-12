#include "usi_engine.h"

#include "book_store.h"
#include "sfen_position.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <sstream>
#include <stdexcept>
#include <thread>

#ifdef _WIN32
#include <io.h>
#else
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace bookminer {

namespace {

bool starts_with(const std::string& text, const std::string& prefix)
{
    return text.size() >= prefix.size() && text.compare(0, prefix.size(), prefix) == 0;
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

int index_of(const std::vector<std::string>& tokens, const std::string& needle)
{
    const auto it = std::find(tokens.begin(), tokens.end(), needle);
    if (it == tokens.end())
        return -1;
    return static_cast<int>(std::distance(tokens.begin(), it));
}

int clamp_cp_eval(int value)
{
    return std::min(ValueEvalClamp, std::max(-ValueEvalClamp, value));
}

int parse_mate_eval(const std::string& value)
{
    if (value == "+")
        return ValueMate - 1;
    if (value == "-")
        return -ValueMate + 1;

    const int mate_ply = std::stoi(value);
    if (mate_ply > 0)
        return ValueMate - mate_ply;
    if (mate_ply < 0)
        return -ValueMate - mate_ply;
    return ValueMate;
}

int legal_move_count_for_position_command(const std::string& command)
{
    const auto parsed = parse_position_command(command);
    auto position = SfenPosition::from_sfen(parsed.start_sfen_with_ply);
    for (const auto& move : parsed.moves)
        position.push_usi(move);
    return static_cast<int>(position.legal_moves().size());
}

std::filesystem::path resolve_local_engine_path(const std::filesystem::path& app_dir, const std::string& path)
{
    std::filesystem::path engine_path(path);
    if (engine_path.is_relative())
        engine_path = app_dir / engine_path;
    return std::filesystem::absolute(engine_path).lexically_normal();
}

#ifdef _WIN32
std::string quote_for_windows_command_line(const std::filesystem::path& path)
{
    std::string s = path.string();
    std::string out = "\"";
    for (char ch : s)
    {
        if (ch == '"')
            out += "\\\"";
        else
            out += ch;
    }
    out += "\"";
    return out;
}
#endif

} // namespace

int evalstr_to_int(const std::string& type, const std::string& value)
{
    if (type == "cp")
        return clamp_cp_eval(std::stoi(value));
    if (type == "mate")
        return parse_mate_eval(value);
    throw std::runtime_error("parse eval error: " + type + "," + value);
}

UsiEngine::UsiEngine(int thread_id, EngineConfig config, std::filesystem::path app_dir)
    : thread_id_(thread_id)
    , config_(std::move(config))
    , app_dir_(std::move(app_dir))
{
}

UsiEngine::~UsiEngine()
{
    close_process();
}

void UsiEngine::start(const LogCallback& log)
{
    if (started_)
        return;

    const bool command_mode = starts_with(config_.path, "ssh ");

#ifdef _WIN32
    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;

    HANDLE child_stdin_read = nullptr;
    HANDLE child_stdout_write = nullptr;
    if (!CreatePipe(&child_stdin_read, &child_stdin_write_, &sa, 0))
        throw std::runtime_error("CreatePipe stdin failed");
    if (!CreatePipe(&child_stdout_read_, &child_stdout_write, &sa, 0))
        throw std::runtime_error("CreatePipe stdout failed");

    SetHandleInformation(child_stdin_write_, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(child_stdout_read_, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOA si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = child_stdin_read;
    si.hStdOutput = child_stdout_write;
    si.hStdError = child_stdout_write;

    PROCESS_INFORMATION pi{};

    std::filesystem::path cwd = app_dir_;
    std::string command_line;
    if (command_mode)
    {
        command_line = config_.path;
    }
    else
    {
        const auto engine_path = resolve_local_engine_path(app_dir_, config_.path);
        if (!std::filesystem::is_regular_file(engine_path))
            throw std::runtime_error("engine not found: " + engine_path.string());
        cwd = engine_path.parent_path();
        command_line = quote_for_windows_command_line(engine_path);
    }

    std::string cwd_string = cwd.string();
    if (!CreateProcessA(nullptr, command_line.data(), nullptr, nullptr, TRUE, 0, nullptr, cwd_string.c_str(), &si, &pi))
        throw std::runtime_error("CreateProcess failed: " + command_line);

    CloseHandle(child_stdin_read);
    CloseHandle(child_stdout_write);
    process_ = pi.hProcess;
    thread_ = pi.hThread;
#else
    int stdin_pipe[2]{};
    int stdout_pipe[2]{};
    if (pipe(stdin_pipe) != 0 || pipe(stdout_pipe) != 0)
        throw std::runtime_error("pipe failed");

    const auto engine_path = command_mode ? std::filesystem::path{} : resolve_local_engine_path(app_dir_, config_.path);
    if (!command_mode && !std::filesystem::is_regular_file(engine_path))
        throw std::runtime_error("engine not found: " + engine_path.string());

    const pid_t pid = fork();
    if (pid < 0)
        throw std::runtime_error("fork failed");

    if (pid == 0)
    {
        if (!command_mode)
            chdir(engine_path.parent_path().c_str());
        else
            chdir(app_dir_.c_str());

        dup2(stdin_pipe[0], STDIN_FILENO);
        dup2(stdout_pipe[1], STDOUT_FILENO);
        dup2(stdout_pipe[1], STDERR_FILENO);

        close(stdin_pipe[0]);
        close(stdin_pipe[1]);
        close(stdout_pipe[0]);
        close(stdout_pipe[1]);

        if (command_mode)
        {
            execl("/bin/sh", "sh", "-lc", config_.path.c_str(), static_cast<char*>(nullptr));
        }
        else
        {
            execl(engine_path.c_str(), engine_path.filename().c_str(), static_cast<char*>(nullptr));
        }
        _exit(127);
    }

    close(stdin_pipe[0]);
    close(stdout_pipe[1]);
    pid_ = pid;
    child_stdin_write_ = stdin_pipe[1];
    child_stdout_read_ = stdout_pipe[0];
#endif

    started_ = true;
    send_line("usi");
    wait_usi("usiok", log);
}

void UsiEngine::isready(const LogCallback& log)
{
    send_line("isready");
    wait_usi("readyok", log);
}

void UsiEngine::usinewgame(const LogCallback& log)
{
    send_line("usinewgame");
    send_line("isready");
    wait_usi("readyok", log);
}

std::vector<EngineMoveInfo> UsiEngine::go(const std::string& position_command, double node_ratio, const LogCallback& log)
{
    const int multipv_step = std::max(1, config_.multipv);
    const int multipv_limit = std::max(1, legal_move_count_for_position_command(position_command));
    int multipv = std::min(multipv_step, multipv_limit);
    const int multipv_delta = std::max(0, config_.multipv_delta);

    send_line("multipv " + std::to_string(multipv));
    send_line("position " + position_command);

    const auto base_nodes = static_cast<std::uint64_t>(std::llround(static_cast<double>(config_.nodes) * node_ratio));
    std::uint64_t nodes = std::max<std::uint64_t>(1, base_nodes);
    const std::uint64_t half_nodes = std::max<std::uint64_t>(1, nodes / 2);
    send_line("go nodes " + std::to_string(nodes));

    std::map<int, EngineMoveInfo> moves;

    while (true)
    {
        auto line = read_line();
        if (!line.has_value())
            throw std::runtime_error("engine output closed during go, thread_id=" + std::to_string(thread_id_));

        if (line->find("Error") != std::string::npos)
            throw std::runtime_error("Engine Error! : " + *line);

        const auto tokens = split_ws(*line);
        if (tokens.empty())
            continue;

        if (tokens[0] == "bestmove")
        {
            std::vector<EngineMoveInfo> node;
            for (int i = 1;; ++i)
            {
                auto it = moves.find(i);
                if (it == moves.end())
                    break;
                node.push_back(it->second);
            }

            if (static_cast<int>(node.size()) == multipv && !node.empty()
                && std::abs(node.front().eval - node.back().eval) <= multipv_delta)
            {
                if (multipv >= multipv_limit)
                    return node;

                multipv = std::min(multipv + multipv_step, multipv_limit);
                nodes = half_nodes;
                send_line("multipv " + std::to_string(multipv));
                send_line("go nodes " + std::to_string(nodes));
                continue;
            }

            return node;
        }

        if (tokens[0] != "info")
            continue;

        const int mpv_index = index_of(tokens, "multipv");
        const int mpv = mpv_index == -1 ? 1 : std::stoi(tokens[static_cast<std::size_t>(mpv_index + 1)]);
        const int score_index = index_of(tokens, "score");
        const int pv_index = index_of(tokens, "pv");
        if (score_index == -1 || pv_index == -1)
            continue;
        if (static_cast<std::size_t>(score_index + 2) >= tokens.size() || static_cast<std::size_t>(pv_index + 1) >= tokens.size())
            continue;

        const std::string move = tokens[static_cast<std::size_t>(pv_index + 1)];
        if (move == "win" || move == "resign")
            continue;

        moves[mpv] = EngineMoveInfo{move, evalstr_to_int(tokens[static_cast<std::size_t>(score_index + 1)], tokens[static_cast<std::size_t>(score_index + 2)])};
        (void)log;
    }
}

void UsiEngine::send_line(const std::string& command)
{
    const std::string line = command + "\n";
#ifdef _WIN32
    DWORD written = 0;
    if (!WriteFile(child_stdin_write_, line.data(), static_cast<DWORD>(line.size()), &written, nullptr))
        throw std::runtime_error("failed to write to engine stdin");
#else
    const char* data = line.data();
    std::size_t remaining = line.size();
    while (remaining > 0)
    {
        const ssize_t n = write(child_stdin_write_, data, remaining);
        if (n < 0)
            throw std::runtime_error("failed to write to engine stdin");
        data += n;
        remaining -= static_cast<std::size_t>(n);
    }
#endif
}

std::optional<std::string> UsiEngine::read_line()
{
    std::string line;
    char ch = '\0';
    while (true)
    {
#ifdef _WIN32
        DWORD read_size = 0;
        if (!ReadFile(child_stdout_read_, &ch, 1, &read_size, nullptr) || read_size == 0)
            return std::nullopt;
#else
        const ssize_t n = read(child_stdout_read_, &ch, 1);
        if (n == 0)
            return std::nullopt;
        if (n < 0)
        {
            if (errno == EINTR)
                continue;
            return std::nullopt;
        }
#endif
        if (ch == '\n')
            break;
        if (ch != '\r')
            line += ch;
    }
    return line;
}

void UsiEngine::wait_usi(const std::string& wait_text, const LogCallback&)
{
    while (true)
    {
        auto line = read_line();
        if (!line.has_value())
            throw std::runtime_error("engine output closed while waiting " + wait_text + ", thread_id=" + std::to_string(thread_id_));
        if (line->find("Error") != std::string::npos)
            throw std::runtime_error("Engine Error! : " + *line);
        if (*line == wait_text)
            return;
    }
}

void UsiEngine::close_process()
{
    if (!started_)
        return;

    try
    {
        send_line("quit");
    }
    catch (...)
    {
    }

#ifdef _WIN32
    if (child_stdin_write_)
        CloseHandle(child_stdin_write_);
    if (child_stdout_read_)
        CloseHandle(child_stdout_read_);
    if (process_)
    {
        WaitForSingleObject(process_, 3000);
        CloseHandle(process_);
    }
    if (thread_)
        CloseHandle(thread_);

    child_stdin_write_ = nullptr;
    child_stdout_read_ = nullptr;
    process_ = nullptr;
    thread_ = nullptr;
#else
    if (child_stdin_write_ >= 0)
        close(child_stdin_write_);
    if (child_stdout_read_ >= 0)
        close(child_stdout_read_);
    if (pid_ > 0)
    {
        int status = 0;
        waitpid(pid_, &status, 0);
    }

    child_stdin_write_ = -1;
    child_stdout_read_ = -1;
    pid_ = -1;
#endif
    started_ = false;
}

std::vector<std::unique_ptr<UsiEngine>> initialize_engines(
    const std::vector<EngineConfig>& configs,
    const std::filesystem::path& app_dir,
    const UsiEngine::LogCallback& log)
{
    int total = 0;
    for (const auto& config : configs)
        total += std::max(1, config.multi);

    log("[EngineInitStart] 0/" + std::to_string(total));

    std::vector<std::unique_ptr<UsiEngine>> engines;
    engines.reserve(static_cast<std::size_t>(total));

    int id = 0;
    int ready = 0;
    for (const auto& config : configs)
    {
        for (int i = 0; i < std::max(1, config.multi); ++i)
        {
            log("  engine " + std::to_string(id + 1) + " , start .. path = " + config.path);
            auto engine = std::make_unique<UsiEngine>(id, config, app_dir);
            engine->start(log);
            engines.push_back(std::move(engine));

            log("[EngineInitProgress] " + std::to_string(static_cast<int>(engines.size())) + "/" + std::to_string(total)
                + " ready=" + std::to_string(ready));

            engines.back()->isready(log);
            ++ready;
            log("[EngineReadyProgress] " + std::to_string(ready) + "/" + std::to_string(total));

            ++id;
            std::this_thread::sleep_for(std::chrono::milliseconds(300));
        }
    }

    log("[EngineInitDone] " + std::to_string(ready) + "/" + std::to_string(total));
    return engines;
}

} // namespace bookminer
