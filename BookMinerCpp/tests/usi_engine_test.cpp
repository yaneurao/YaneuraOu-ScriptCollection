#include "usi_engine.h"
#include "sfen_position.h"
#include <iostream>
#include <chrono>
#include <thread>
#include <stdexcept>

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
    if (argc != 2 && argc != 3) return 2;
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
    for (const auto& move : result.moves)
        std::cout << move.move << ' ' << move.eval << '\n';
}
