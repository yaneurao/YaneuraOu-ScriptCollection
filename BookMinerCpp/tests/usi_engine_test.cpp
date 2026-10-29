#include "usi_engine.h"
#include "sfen_position.h"
#include <iostream>
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
    if (argc != 2) return 2;
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
