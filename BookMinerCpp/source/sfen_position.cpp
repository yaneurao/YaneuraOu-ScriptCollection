#include "sfen_position.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <deque>
#include <mutex>
#include <sstream>
#include <stdexcept>

#include "bitboard.h"
#include "movegen.h"
#include "position.h"
#include "types.h"
#include "usi.h"

static_assert(sizeof(YaneuraOu::PackedSfen) == 32, "YaneuraOu::PackedSfen must be 32 bytes");

namespace bookminer {

namespace {

std::once_flag g_yaneuraou_init_once;

void ensure_yaneuraou_initialized()
{
    std::call_once(g_yaneuraou_init_once, [] {
        YaneuraOu::Bitboards::init();
        YaneuraOu::Position::init();
    });
}

std::vector<std::string> split_ws(const std::string& text)
{
    std::istringstream iss(text);
    std::vector<std::string> out;
    std::string token;
    while (iss >> token)
        out.push_back(token);
    return out;
}

bool starts_with(const std::string& text, const std::string& prefix)
{
    return text.size() >= prefix.size() && text.compare(0, prefix.size(), prefix) == 0;
}

YaneuraOu::Move checked_usi_to_move(const YaneuraOu::Position& pos, const std::string& usi)
{
    auto move = pos.to_move(YaneuraOu::USIEngine::to_move16(usi));
    if (move.is_ok() && pos.pseudo_legal_s<true>(move) && pos.legal(move))
        return move;
    throw std::runtime_error("illegal usi move: " + usi + " in " + pos.sfen());
}

std::string normalize_sfen_input(std::string sfen)
{
    if (starts_with(sfen, "sfen "))
        sfen.erase(0, 5);
    if (sfen == "startpos")
        return StartSfenPly1;
    return sfen;
}

} // namespace

class SfenPosition::Impl {
public:
    Impl()
    {
        ensure_yaneuraou_initialized();
        reset(StartSfenPly1);
    }

    explicit Impl(const std::string& sfen)
    {
        ensure_yaneuraou_initialized();
        reset(sfen);
    }

    Impl(const Impl& other)
    {
        ensure_yaneuraou_initialized();
        reset(other.position.sfen());
    }

    void reset(const std::string& sfen)
    {
        states.clear();
        states.emplace_back(YaneuraOu::StateInfo{});
        auto error = position.set(normalize_sfen_input(sfen), &states.back());
        if (error.has_value())
            throw std::runtime_error(error->what());
    }

    void push_usi(const std::string& move)
    {
        const auto yane_move = checked_usi_to_move(position, move);
        states.emplace_back(YaneuraOu::StateInfo{});
        position.do_move(yane_move, states.back());
    }

    std::vector<std::string> legal_moves() const
    {
        std::vector<std::string> moves;
        for (const auto move : YaneuraOu::MoveList<YaneuraOu::LEGAL>(position))
            moves.push_back(YaneuraOu::USIEngine::move(move));
        return moves;
    }

    YaneuraOu::Position position;
    std::deque<YaneuraOu::StateInfo> states;
};

SfenPosition::SfenPosition()
    : impl_(std::make_unique<Impl>())
{
}

SfenPosition::SfenPosition(const SfenPosition& other)
    : impl_(std::make_unique<Impl>(*other.impl_))
{
}

SfenPosition& SfenPosition::operator=(const SfenPosition& other)
{
    if (this != &other)
        impl_ = std::make_unique<Impl>(*other.impl_);
    return *this;
}

SfenPosition::SfenPosition(SfenPosition&&) noexcept = default;
SfenPosition& SfenPosition::operator=(SfenPosition&&) noexcept = default;
SfenPosition::~SfenPosition() = default;

SfenPosition SfenPosition::from_sfen(const std::string& sfen_with_optional_ply)
{
    SfenPosition position;
    position.impl_ = std::make_unique<Impl>(sfen_with_optional_ply);
    return position;
}

std::string SfenPosition::sfen() const
{
    return impl_->position.sfen(-1);
}

std::string SfenPosition::sfen_with_ply() const
{
    return impl_->position.sfen();
}

int SfenPosition::ply() const noexcept
{
    return impl_->position.game_ply();
}

void SfenPosition::push_usi(const std::string& move)
{
    impl_->push_usi(move);
}

std::vector<std::string> SfenPosition::legal_moves() const
{
    return impl_->legal_moves();
}

std::array<std::uint8_t, 32> pack_sfen_bytes(const std::string& sfen_with_optional_ply)
{
    ensure_yaneuraou_initialized();

    std::deque<YaneuraOu::StateInfo> states;
    states.emplace_back(YaneuraOu::StateInfo{});

    YaneuraOu::Position position;
    auto error = position.set(normalize_sfen_input(sfen_with_optional_ply), &states.back());
    if (error.has_value())
        throw std::runtime_error(error->what());

    YaneuraOu::PackedSfen packed{};
    position.sfen_pack(packed);

    std::array<std::uint8_t, 32> bytes{};
    std::copy(std::begin(packed.data), std::end(packed.data), bytes.begin());
    return bytes;
}

std::array<std::uint8_t, 32> flip_packed_sfen_bytes(const std::array<std::uint8_t, 32>& packed_sfen)
{
    ensure_yaneuraou_initialized();

    YaneuraOu::PackedSfen packed{};
    std::copy(packed_sfen.begin(), packed_sfen.end(), std::begin(packed.data));
    packed.flip();

    std::array<std::uint8_t, 32> bytes{};
    std::copy(std::begin(packed.data), std::end(packed.data), bytes.begin());
    return bytes;
}

std::string unpack_sfen_bytes(const std::array<std::uint8_t, 32>& packed_sfen)
{
    ensure_yaneuraou_initialized();

    YaneuraOu::PackedSfen packed{};
    std::copy(packed_sfen.begin(), packed_sfen.end(), std::begin(packed.data));
    return YaneuraOu::Position::sfen_unpack(packed);
}

std::string flipped_sfen(const std::string& sfen_with_optional_ply)
{
    ensure_yaneuraou_initialized();

    std::deque<YaneuraOu::StateInfo> states;
    states.emplace_back(YaneuraOu::StateInfo{});

    YaneuraOu::Position position;
    auto error = position.set(normalize_sfen_input(sfen_with_optional_ply), &states.back());
    if (error.has_value())
        throw std::runtime_error(error->what());

    return position.flipped_sfen(-1);
}

std::uint16_t flipped_move16(std::uint16_t move16)
{
    return YaneuraOu::flip_move(YaneuraOu::Move16(move16)).to_u16();
}

ParsedPositionCommand parse_position_command(const std::string& command)
{
    std::string text = command;
    text.erase(text.begin(), std::find_if(text.begin(), text.end(), [](unsigned char ch) {
        return !std::isspace(ch);
    }));
    while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back())))
        text.pop_back();

    if (starts_with(text, "position "))
        text.erase(0, 9);

    std::string start = text;
    std::string moves_text;
    const auto moves_pos = text.find(" moves ");
    if (moves_pos != std::string::npos)
    {
        start = text.substr(0, moves_pos);
        moves_text = text.substr(moves_pos + 7);
    }

    ParsedPositionCommand parsed;
    if (start == "startpos")
        parsed.start_sfen_with_ply = StartSfenPly1;
    else if (starts_with(start, "sfen "))
        parsed.start_sfen_with_ply = start.substr(5);
    else
        parsed.start_sfen_with_ply = start;

    parsed.moves = split_ws(moves_text);
    return parsed;
}

} // namespace bookminer
