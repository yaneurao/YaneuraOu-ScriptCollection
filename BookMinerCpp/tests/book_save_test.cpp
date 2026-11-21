#include "book_store.h"
#include "sfen_position.h"
#include <algorithm>
#include <array>
#include <fstream>
#include <cstdint>
#include <filesystem>
#include <limits>
#include <map>
#include <stdexcept>
#include <system_error>
#include <vector>

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    const std::filesystem::path dir(argv[1]);
    {
        const auto path = dir / std::filesystem::path(u8"定跡.YBB");
        bookminer::BookStore source, loaded;
        source.merge_position(bookminer::StartSfenPly1, 1, {{bookminer::move16_from_usi("7g7f"), 42, 0}});
        source.save_yaneuraou_book(path, std::nullopt, nullptr, nullptr);
        loaded.load_yaneuraou_book(path, false, nullptr, nullptr);
        const auto found = loaded.find_position_copy(bookminer::StartSfenPly1);
        auto probe = bookminer::open_book_probe(path);
        const auto probed = probe->find_position_copy(bookminer::StartSfenPly1);
        if (!found || !probed || found->moves.empty() || probed->moves.empty()
            || found->moves.front().eval != 42 || probed->moves.front().eval != 42)
            throw std::runtime_error("uppercase .YBB was not written, read and probed as binary");
        if (bookminer::is_yane_bin_book_path(dir / "book-INDEX.YBB"))
            throw std::runtime_error("legacy split binary name accepted as a single ybb");
        bool rejected = false;
        try { (void)bookminer::open_book_probe(dir / "book-INDEX.YBB"); }
        catch (const std::runtime_error&) { rejected = true; }
        if (!rejected) throw std::runtime_error("legacy split binary probe was not rejected");
    }
    {
        const auto output = dir / "target.ybb";
        const auto old_temporary = dir / "tmp-target.ybb";
        { std::ofstream out(old_temporary); out << "unrelated"; }
        const auto first = bookminer::temp_book_path(output);
        const auto second = bookminer::temp_book_path(output);
        if (first == second || first.extension() != ".ybb" || second.extension() != ".ybb"
            || !std::filesystem::is_regular_file(first) || !std::filesystem::is_regular_file(second))
            throw std::runtime_error("temporary book paths are not unique, reserved, and format-preserving");
        std::filesystem::remove(first);
        std::filesystem::remove(second);
        bookminer::BookStore book;
        book.merge_position(bookminer::StartSfenPly1, 1, {{bookminer::move16_from_usi("7g7f"), 5, 0}});
        book.save_yaneuraou_book(output, std::nullopt, nullptr, nullptr);
        std::ifstream in(old_temporary);
        std::string content;
        in >> content;
        if (content != "unrelated")
            throw std::runtime_error("book save damaged an unrelated old temporary file");
    }
    {
        const auto input = dir / "duplicate-moves.db";
        { std::ofstream out(input); out << "#YANEURAOU-DB2016 1.00\nsfen " << bookminer::StartSfenPly1
              << "\n7g7f none 100 1\n2g2f none 30 2\n7g7f none 20 3\nsfen " << bookminer::StartSfenPly1
              << "\n7g7f none 15 4\n"; }
        bookminer::BookStore book;
        book.load_yaneuraou_book(input, false, nullptr, nullptr);
        const auto position = book.find_position_copy(bookminer::StartSfenPly1);
        auto probe = bookminer::open_book_probe(input);
        const auto probed = probe->find_position_copy(bookminer::StartSfenPly1);
        for (const auto* p : {&position, &probed})
            if (!*p || (*p)->moves.size() != 2 || (*p)->moves[0].move16 != bookminer::move16_from_usi("2g2f")
                || (*p)->moves[1].eval != 15 || (*p)->moves[1].depth != 4)
                throw std::runtime_error("duplicate moves in one position were not merged");
    }
    {
        const auto input = dir / "duplicate-test.ybb";
        const auto key = bookminer::PackedSfen::from_sfen(bookminer::StartSfenPly1);
        {
            std::ofstream out(input, std::ios::binary);
            out.write("YANE-BINBOOK-V1\0", 16);
            auto u64 = [&](std::uint64_t x) { for (int i = 0; i < 8; ++i) out.put(static_cast<char>(x >> (8 * i))); };
            auto u16 = [&](std::uint16_t x) { out.put(static_cast<char>(x)); out.put(static_cast<char>(x >> 8)); };
            u64(1); u64(1);
            out.write(reinterpret_cast<const char*>(key.bytes.data()), key.bytes.size());
            u64(0); u16(1); u16(3);
            u16(bookminer::move16_from_usi("7g7f")); u16(100); u16(1);
            u16(bookminer::move16_from_usi("2g2f")); u16(30); u16(2);
            u16(bookminer::move16_from_usi("7g7f")); u16(20); u16(3);
        }
        bookminer::BookStore book;
        book.load_yaneuraou_book(input, false, nullptr, nullptr);
        const auto position = book.find_position_copy(bookminer::StartSfenPly1);
        auto probe = bookminer::open_book_probe(input);
        const auto probed = probe->find_position_copy(bookminer::StartSfenPly1);
        for (const auto* p : {&position, &probed})
            if (!*p || (*p)->moves.size() != 2 || (*p)->moves[0].move16 != bookminer::move16_from_usi("2g2f")
                || (*p)->moves[1].eval != 20 || (*p)->moves[1].depth != 3)
                throw std::runtime_error("duplicate YBB moves in one position were not merged");
    }
    const auto existing = dir / "existing.db";
    { std::ofstream out(existing); out << "original"; }
    try
    {
        bookminer::replace_book_file(dir / "missing.tmp", existing);
        throw std::runtime_error("replacement unexpectedly succeeded");
    }
    catch (const std::filesystem::filesystem_error&) {}
    catch (const std::system_error&) {}
    {
        std::ifstream in(existing);
        std::string content;
        in >> content;
        if (content != "original")
            throw std::runtime_error("failed replacement damaged existing book");
    }
    {
        bookminer::BookStore book;
        const std::vector<bookminer::MoveInfo> moves = {{bookminer::move16_from_usi("7g7f"), 20, 0}};
        book.merge_position(bookminer::StartSfenPly1, 20, moves);
        if (book.count_save_positions(10) != 0)
            throw std::runtime_error("initial ply limit was not applied");
        book.update_position_ply(bookminer::StartSfenPly1, 5);
        if (book.count_save_positions(10) != 1 || book.find_position_copy(bookminer::StartSfenPly1)->ply != 5)
            throw std::runtime_error("shorter path did not update saved ply");
        const auto revision = book.revision();
        book.update_position_ply(bookminer::StartSfenPly1, 10);
        if (book.revision() != revision)
            throw std::runtime_error("longer path changed stored ply");
        book.merge_position(bookminer::StartSfenPly1, 3, moves);
        if (book.find_position_copy(bookminer::StartSfenPly1)->ply != 3)
            throw std::runtime_error("merge did not retain the shortest ply");
    }
    {
        const auto input = dir / "duplicate-ply.db";
        std::ofstream out(input);
        out << bookminer::YaneuraOuBookHeaderV1 << "\nsfen " << bookminer::StartSfen
            << " 20\n7g7f none 10 0\nsfen " << bookminer::StartSfen
            << " 5\n2g2f none 5 0\n7g7f none 30 7\n";
        out.close();
        bookminer::BookStore book;
        book.load_yaneuraou_book(input, false, nullptr, nullptr);
        const auto loaded = book.find_position_copy(bookminer::StartSfenPly1);
        if (!loaded || loaded->ply != 5 || loaded->moves.size() != 2
            || loaded->moves.front().eval != 30 || loaded->moves.front().depth != 7)
            throw std::runtime_error("import did not merge duplicate position moves and ply");
        auto probe = bookminer::open_book_probe(input);
        const auto found = probe->find_position_copy(bookminer::StartSfenPly1);
        if (!found || found->ply != 5 || found->moves.size() != 2
            || found->moves.front().eval != 30 || found->moves.front().depth != 7)
            throw std::runtime_error("db probe did not merge duplicate positions");
    }
    {
        // Check binary search at many offsets, including duplicate SFENs in
        // the middle of the file and a last line without a newline.
        std::map<std::string, std::string> entries;
        auto board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        for (int i = 0; i < 30; ++i)
        {
            const auto moves = board.legal_moves();
            if (moves.empty()) break;
            entries.emplace(board.sfen(), moves.front());
            board.push_usi(moves.front());
        }
        const auto path = dir / "probe-many.db";
        {
            std::ofstream out(path);
            out << bookminer::YaneuraOuBookHeaderV1 << '\n';
            for (const auto& [sfen, move] : entries)
                out << "sfen " << sfen << " 20\n" << move << " none 10 0\n"
                    << "sfen " << sfen << " 5\n" << move << " none 20 0\n";
            out << "# end without newline";
        }
        bookminer::BookStore loaded;
        loaded.load_yaneuraou_book(path, false, nullptr, nullptr);
        auto probe = bookminer::open_book_probe(path);
        for (const auto& [sfen, _move] : entries)
        {
            const auto expected = loaded.find_position_copy(sfen);
            const auto found = probe->find_position_copy(sfen);
            if (!expected || !found || expected->ply != found->ply
                || expected->moves.size() != found->moves.size()
                || found->moves.front().eval != expected->moves.front().eval)
                throw std::runtime_error("db probe disagrees with loaded book");
        }
    }
    const auto min = std::numeric_limits<std::int16_t>::min();
    for (const auto& values : std::vector<std::vector<std::int16_t>>{{10, 100, 30}, {100, 100, 20}, {min, min, min}})
    {
        bookminer::BookStore book;
        book.merge_position(bookminer::StartSfenPly1, 1, {
            {bookminer::move16_from_usi("7g7f"), values[0], 0},
            {bookminer::move16_from_usi("2g2f"), values[1], 0},
            {bookminer::move16_from_usi("3g3f"), values[2], 0},
        });
        for (const char* name : {"book.db", "book.ybb"})
        {
            const auto path = dir / name;
            book.save_yaneuraou_book(path, std::nullopt, nullptr, nullptr);
            bookminer::BookStore loaded;
            loaded.load_yaneuraou_book(path, false, nullptr, nullptr);
            const auto position = loaded.find_position_copy(bookminer::StartSfenPly1);
            if (!position || position->moves.size() != values.size())
                throw std::runtime_error("saved position missing");
            const auto& moves = position->moves;
            if (moves.front().eval != *std::max_element(values.begin(), values.end()))
                throw std::runtime_error("first score is not the original maximum");
            for (std::size_t i = 1; i < moves.size(); ++i)
                if (moves[i - 1].eval < moves[i].eval)
                    throw std::runtime_error("saved scores are not descending");
        }
    }
    // Imported files need not already be ordered by score.
    const auto db = dir / "unsorted.db";
    {
        std::ofstream out(db);
        out << bookminer::YaneuraOuBookHeaderV1 << "\nsfen " << bookminer::StartSfenPly1
            << "\n7g7f none -20 0\n2g2f none 100 0\n3g3f none 10 0\n";
    }
    auto check_import = [&](const std::filesystem::path& path) {
        bookminer::BookStore loaded;
        loaded.load_yaneuraou_book(path, false, nullptr, nullptr);
        auto position = loaded.find_position_copy(bookminer::StartSfenPly1);
        if (!position || position->moves.front().eval != 100)
            throw std::runtime_error("import did not put the best score first");
        auto probe = bookminer::open_book_probe(path);
        position = probe->find_position_copy(bookminer::StartSfenPly1);
        if (!position || position->moves.front().eval != 100)
            throw std::runtime_error("probe did not put the best score first");
    };
    check_import(db);
    bookminer::BookStore imported;
    imported.load_yaneuraou_book(db, false, nullptr, nullptr);
    const auto ybb = dir / "unsorted.ybb";
    imported.save_yaneuraou_book(ybb, std::nullopt, nullptr, nullptr);
    {
        // A depth-enabled YBB move occupies six bytes. Swap first and last.
        std::fstream file(ybb, std::ios::binary | std::ios::in | std::ios::out);
        file.seekg(-18, std::ios::end);
        const auto start = file.tellg();
        std::array<char, 18> records{};
        file.read(records.data(), records.size());
        for (int i = 0; i < 6; ++i) std::swap(records[i], records[12 + i]);
        file.seekp(start);
        file.write(records.data(), records.size());
    }
    check_import(ybb);

    // A failed import must leave the prior book available.
    bookminer::BookStore preserved;
    preserved.merge_position(bookminer::StartSfenPly1, 1, {
        {bookminer::move16_from_usi("7g7f"), 123, 0}});
    for (const char* name : {"missing.db", "missing.ybb"}) {
        bool failed = false;
        try { preserved.load_yaneuraou_book(dir / name, false, nullptr, nullptr); }
        catch (const std::runtime_error&) { failed = true; }
        auto existing = preserved.find_position_copy(bookminer::StartSfenPly1);
        if (!failed || !existing || existing->moves.front().eval != 123)
            throw std::runtime_error("failed import erased existing book");
    }
    for (const char* name : {"broken.db", "broken.ybb"}) {
        const auto path = dir / name;
        {
            std::ofstream out(path, std::ios::binary);
            if (path.extension() == ".db")
                out << bookminer::YaneuraOuBookHeaderV1 << "\nsfen " << bookminer::StartSfenPly1
                    << "\n7g7f none invalid 0\n";
            else
                out << "invalid ybb";
        }
        bool failed = false;
        try { preserved.load_yaneuraou_book(path, false, nullptr, nullptr); }
        catch (const std::runtime_error&) { failed = true; }
        auto existing = preserved.find_position_copy(bookminer::StartSfenPly1);
        if (!failed || !existing || existing->moves.front().eval != 123)
            throw std::runtime_error("malformed import erased existing book");
    }
    const auto oversized = dir / "oversized.db";
    {
        std::ofstream out(oversized);
        out << bookminer::YaneuraOuBookHeaderV1 << "\nsfen " << bookminer::StartSfenPly1
            << "\n7g7f none 100000 0\n";
    }
    bool rejected = false;
    try { preserved.load_yaneuraou_book(oversized, false, nullptr, nullptr); }
    catch (const std::runtime_error&) { rejected = true; }
    if (!rejected || preserved.find_position_copy(bookminer::StartSfenPly1)->moves.front().eval != 123)
        throw std::runtime_error("out-of-range score was not safely rejected");
    bookminer::BookStore normalized;
    normalized.load_yaneuraou_book(oversized, true, nullptr, nullptr);
    const auto old_book = normalized.find_position_copy(bookminer::StartSfenPly1);
    if (!old_book || old_book->moves.front().eval != bookminer::ValueMate)
        throw std::runtime_error("legacy score normalization failed");
    if (bookminer::normalize_book_eval(std::numeric_limits<int>::min()) != std::numeric_limits<std::int16_t>::min())
        throw std::runtime_error("minimum int normalization failed");

}
