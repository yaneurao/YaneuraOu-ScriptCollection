#include "book_store.h"
#include "sfen_position.h"
#include <algorithm>
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
}
