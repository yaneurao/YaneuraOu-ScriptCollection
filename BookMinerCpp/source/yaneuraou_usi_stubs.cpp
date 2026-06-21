#include "position.h"
#include "tt.h"
#include "types.h"
#include "usi.h"
#include "usioption.h"

#include <sstream>
#include <cstdlib>
#include <utility>

namespace YaneuraOu {

namespace {

Square parse_usi_square(char file, char rank)
{
    if (file < '1' || file > '9' || rank < 'a' || rank > 'i')
        return SQ_NB;
    return Square((file - '1') * 9 + (rank - 'a'));
}

PieceType parse_drop_piece(char piece)
{
    switch (piece)
    {
    case 'P': return PAWN;
    case 'L': return LANCE;
    case 'N': return KNIGHT;
    case 'S': return SILVER;
    case 'B': return BISHOP;
    case 'R': return ROOK;
    case 'G': return GOLD;
    default: return NO_PIECE_TYPE;
    }
}

std::string square_to_usi(Square sq)
{
    std::string out;
    out += char('1' + int(sq) / 9);
    out += char('a' + int(sq) % 9);
    return out;
}

char drop_piece_to_char(PieceType pt)
{
    switch (pt)
    {
    case PAWN: return 'P';
    case LANCE: return 'L';
    case KNIGHT: return 'N';
    case SILVER: return 'S';
    case BISHOP: return 'B';
    case ROOK: return 'R';
    case GOLD: return 'G';
    default: return '?';
    }
}

} // namespace

std::string USIEngine::move(Move m)
{
    return move(m.to_move16());
}

std::string USIEngine::move(Move16 m)
{
    if (!m.is_ok())
    {
        return m == Move16::resign() ? "resign"
            : m == Move16::win()     ? "win"
            : m == Move16::null()    ? "null"
            : m == Move16::none()    ? "none"
                                     : "";
    }

    if (m.is_drop())
    {
        std::string out;
        out += drop_piece_to_char(m.move_dropped_piece());
        out += '*';
        out += square_to_usi(m.to_sq());
        return out;
    }

    std::string out = square_to_usi(m.from_sq()) + square_to_usi(m.to_sq());
    if (m.is_promote())
        out += '+';
    return out;
}

Move16 USIEngine::to_move16(const std::string& str)
{
    if (str == "resign")
        return Move16::resign();
    if (str == "win")
        return Move16::win();
    if (str == "0000" || str == "null" || str == "pass")
        return Move16::null();
    if (str.size() <= 3)
        return Move16::none();

    const Square to = parse_usi_square(str[2], str[3]);
    if (!is_ok(to))
        return Move16::none();

    const bool promote = str.size() == 5 && str[4] == '+';
    const bool drop = str[1] == '*';
    if (drop)
    {
        const PieceType piece = parse_drop_piece(str[0]);
        if (piece == NO_PIECE_TYPE)
            return Move16::none();
        return make_move_drop16(piece, to);
    }

    const Square from = parse_usi_square(str[0], str[1]);
    if (!is_ok(from))
        return Move16::none();
    return promote ? make_move_promote16(from, to) : make_move16(from, to);
}

Move USIEngine::to_move(const Position& pos, std::string str)
{
    Move move = pos.to_move(to_move16(str));
    if (move.is_ok() && pos.pseudo_legal_s<true>(move) && pos.legal(move))
        return move;
    return Move::none();
}

namespace Test {

void UnitTester::test(const std::string&, bool)
{
}

} // namespace Test

TTEntry* TranspositionTable::first_entry(const Key&, Color) const
{
    return nullptr;
}

void prefetch(const void*)
{
}

std::ostream& operator<<(std::ostream& os, SyncCout)
{
    return os;
}

namespace Tools {

void exit()
{
    std::abort();
}

} // namespace Tools

namespace {

const Option& stub_option()
{
    static Option option(int64_t(0), int64_t(0), int64_t(0));
    return option;
}

} // namespace

const Option& OptionsMap::operator[](const std::string&) const
{
    return stub_option();
}

Option::Option(int64_t value, int64_t minv, int64_t maxv, OnChange on_change_)
    : defaultValue(std::to_string(value)),
      currentValue(std::to_string(value)),
      type("spin"),
      min(minv),
      max(maxv),
      idx(0),
      on_change(std::move(on_change_))
{
}

Option::operator int64_t() const
{
    return 0;
}

} // namespace YaneuraOu
