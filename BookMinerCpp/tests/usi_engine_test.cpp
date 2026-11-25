#include "usi_engine.h"
#include "sfen_position.h"
#include <iostream>
#include <chrono>
#include <thread>
#include <stdexcept>
#include <filesystem>
#if defined(__linux__)
#include <fcntl.h>
#include <sys/resource.h>
#include <unistd.h>
#endif

// The protocol test does not depend on the board implementation.
namespace bookminer {
class SfenPosition::Impl {};
SfenPosition::SfenPosition() = default;
SfenPosition::~SfenPosition() = default;
SfenPosition SfenPosition::from_sfen(const std::string&) { return {}; }
void SfenPosition::push_usi(const std::string&) {}
std::vector<std::string> SfenPosition::legal_moves() const { return {"a", "b", "c", "d"}; }
ParsedPositionCommand parse_position_command(const std::string&) { return {}; }
}

int main(int argc, char** argv) {
    if (argc == 4 && std::string(argv[3]) == "--inherit-check") {
        bookminer::EngineConfig first, second;
        first.path = argv[1];
        second.path = argv[2];
        auto engine1 = std::make_unique<bookminer::UsiEngine>(0, first, ".");
        auto engine2 = std::make_unique<bookminer::UsiEngine>(1, second, ".");
        engine1->start([](const std::string&) {});
        engine2->start([](const std::string&) {});
        engine1.reset();
        engine2.reset();
        return 0;
    }
    if (argc != 2 && argc != 3) return 2;
#if defined(__linux__)
    if (argc == 3 && std::string(argv[2]) == "--pipe-fail-check") {
        const rlimit limit{64, 64};
        if (setrlimit(RLIMIT_NOFILE, &limit) != 0) return 4;
        std::vector<int> fillers;
        while (true) {
            const int fd = open("/dev/null", O_RDONLY);
            if (fd < 0) break;
            fillers.push_back(fd);
        }
        if (fillers.size() < 3) return 4;
        for (int i = 0; i < 3; ++i) { close(fillers.back()); fillers.pop_back(); }
        const auto fd_count = [] {
            return std::distance(std::filesystem::directory_iterator("/proc/self/fd"),
                                 std::filesystem::directory_iterator());
        };
        const auto before = fd_count();
        bookminer::EngineConfig config;
        config.path = argv[1];
        try {
            bookminer::UsiEngine engine(0, config, ".");
            engine.start([](const std::string&) {});
            return 3;
        } catch (const std::exception&) {}
        const auto after = fd_count();
        for (int fd : fillers) close(fd);
        if (after != before) {
            std::cerr << "failed pipe creation leaked file descriptors\n";
            return 1;
        }
        return 0;
    }
    if (argc == 3 && std::string(argv[2]) == "--missing-fd-check") {
        const auto fd_count = [] {
            return std::distance(std::filesystem::directory_iterator("/proc/self/fd"),
                                 std::filesystem::directory_iterator());
        };
        const auto before = fd_count();
        bookminer::EngineConfig config;
        config.path = argv[1];
        for (int i = 0; i < 32; ++i) {
            try {
                bookminer::UsiEngine engine(0, config, ".");
                engine.start([](const std::string&) {});
                return 3;
            } catch (const std::exception&) {}
        }
        if (fd_count() != before) {
            std::cerr << "engine startup leaked file descriptors\n";
            return 1;
        }
        return 0;
    }
#endif
    if (argc == 3 && std::string(argv[2]) == "--broken-pipe") {
        bookminer::EngineConfig config;
        config.path = argv[1];
        try {
            bookminer::UsiEngine engine(0, config, ".");
            engine.start([](const std::string&) {});
            std::this_thread::sleep_for(std::chrono::milliseconds(300));
            engine.isready([](const std::string&) {});
        } catch (const std::exception& error) {
            std::cerr << error.what() << '\n';
            return 0;
        }
        return 3;
    }
    if (argc == 3) {
        bookminer::EngineConfig first, missing;
        first.path = argv[1];
        missing.path = argv[2];
        try {
            bookminer::initialize_engines({first, missing}, ".", [](const std::string&) {});
        } catch (const std::exception& error) {
            std::cerr << error.what() << '\n';
            return 0;
        }
        return 3;
    }
    bookminer::EngineConfig config;
    config.path = argv[1];
    config.multipv = 2;
    config.multipv_delta = 10;
    config.nodes = 100;
    bookminer::UsiEngine engine(0, config, ".");
    auto log = [](const std::string& s) { std::cerr << s << '\n'; };
    engine.start(log);
    engine.isready(log);
    auto result = engine.go("startpos", 1.0, log);
    std::cerr << "searched_nodes=" << result.searched_nodes << '\n';
    for (const auto& move : result.moves)
        std::cout << move.move << ' ' << move.eval << '\n';
}
