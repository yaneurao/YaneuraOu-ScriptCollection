#pragma once

#include "config.h"

#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <map>
#include <optional>
#include <string>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <sys/types.h>
#endif

namespace bookminer {

struct EngineMoveInfo {
    std::string move;
    int eval = 0;
};

class UsiEngine {
public:
    using LogCallback = std::function<void(const std::string&)>;

    UsiEngine(int thread_id, EngineConfig config, std::filesystem::path app_dir);
    ~UsiEngine();

    UsiEngine(const UsiEngine&) = delete;
    UsiEngine& operator=(const UsiEngine&) = delete;

    UsiEngine(UsiEngine&&) noexcept = default;
    UsiEngine& operator=(UsiEngine&&) noexcept = default;

    void start(const LogCallback& log);
    void isready(const LogCallback& log);
    void usinewgame(const LogCallback& log);

    std::vector<EngineMoveInfo> go(const std::string& position_command, double node_ratio, const LogCallback& log);

    int thread_id() const noexcept { return thread_id_; }
    const EngineConfig& config() const noexcept { return config_; }
    bool started() const noexcept { return started_; }

private:
    void send_line(const std::string& command);
    std::optional<std::string> read_line();
    void wait_usi(const std::string& wait_text, const LogCallback& log);
    void close_process();

    int thread_id_ = 0;
    EngineConfig config_;
    std::filesystem::path app_dir_;
    bool started_ = false;

#ifdef _WIN32
    HANDLE child_stdin_write_ = nullptr;
    HANDLE child_stdout_read_ = nullptr;
    HANDLE process_ = nullptr;
    HANDLE thread_ = nullptr;
#else
    pid_t pid_ = -1;
    int child_stdin_write_ = -1;
    int child_stdout_read_ = -1;
#endif
};

int evalstr_to_int(const std::string& type, const std::string& value);
std::vector<std::unique_ptr<UsiEngine>> initialize_engines(
    const std::vector<EngineConfig>& configs,
    const std::filesystem::path& app_dir,
    const UsiEngine::LogCallback& log);

} // namespace bookminer
