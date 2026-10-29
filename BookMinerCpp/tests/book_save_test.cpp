#include "book_store.h"
#include "sfen_position.h"
#include <algorithm>
#include <array>
#include <fstream>
#include <cstdint>
#include <filesystem>
#include <limits>
#include <stdexcept>
#include <vector>

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    const std::filesystem::path dir(argv[1]);
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
