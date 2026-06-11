#pragma once

#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace bookminer {

inline constexpr const char* StartSfen = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b -";
inline constexpr const char* StartSfenPly1 = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1";

struct ParsedPositionCommand {
    std::string start_sfen_with_ply;
    std::vector<std::string> moves;
};

class SfenPosition {
public:
    SfenPosition();
    SfenPosition(const SfenPosition& other);
    SfenPosition& operator=(const SfenPosition& other);
    SfenPosition(SfenPosition&&) noexcept;
    SfenPosition& operator=(SfenPosition&&) noexcept;
    ~SfenPosition();

    static SfenPosition from_sfen(const std::string& sfen_with_optional_ply);

    std::string sfen() const;
    std::string sfen_with_ply() const;
    int ply() const noexcept;

    void push_usi(const std::string& move);
    std::vector<std::string> legal_moves() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

ParsedPositionCommand parse_position_command(const std::string& command);
std::array<std::uint8_t, 32> pack_sfen_bytes(const std::string& sfen_with_optional_ply);
std::array<std::uint8_t, 32> flip_packed_sfen_bytes(const std::array<std::uint8_t, 32>& packed_sfen);
std::string unpack_sfen_bytes(const std::array<std::uint8_t, 32>& packed_sfen);
std::string flipped_sfen(const std::string& sfen_with_optional_ply);
std::uint16_t flipped_move16(std::uint16_t move16);

} // namespace bookminer
