#define main bookminer_program_main
#include "../source/main.cpp"
#undef main

int main(int argc, char** argv)
{
    if (argc != 2)
        return 2;
    {
        TaskQueue queue;
        queue.stop(true);
        queue.push(Task{});
        if (queue.pop().has_value())
            throw std::runtime_error("task was requeued after shutdown");
    }
    {
        bookminer::BookStore seen_book;
        seen_book.merge_position(bookminer::StartSfenPly1, 20, {
            {bookminer::move16_from_usi("7g7f"), 10, 0},
        });
        std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> visited;
        visited.insert(bookminer::PackedSfen::from_sfen(bookminer::StartSfenPly1));
        bookminer::EngineConfig config;
        bookminer::UsiEngine unused_engine(0, config, ".");
        int last_thinking_ply = 0;
        (void)think_sfen_once(seen_book, unused_engine, bookminer::StartSfenPly1,
                              5, last_thinking_ply, 100, visited);
        if (seen_book.find_position_copy(bookminer::StartSfenPly1)->ply != 5)
            throw std::runtime_error("visited position did not retain a shorter path");
        bookminer::BookStore existing_book;
        existing_book.merge_position(bookminer::StartSfenPly1, 20, {
            {bookminer::move16_from_usi("7g7f"), 10, 0},
        });
        visited.clear();
        (void)think_sfen_once(existing_book, unused_engine, bookminer::StartSfenPly1,
                              5, last_thinking_ply, 100, visited);
        if (existing_book.find_position_copy(bookminer::StartSfenPly1)->ply != 5)
            throw std::runtime_error("existing position did not retain a shorter path");
    }
    const std::filesystem::path roots_path(argv[1]);
    {
        const auto input = roots_path.parent_path() / "think-input.txt";
        {
            std::ofstream out(input, std::ios::binary);
            out << "startpos\n"
                   "startpos, book_extend_ply=3, eval_limit=500, game_ply_limit=100\n";
        }
        const auto commands = read_position_commands_file(input, 400, 200, 10);
        if (commands.size() != 1 || commands[0].position_command != "startpos"
            || commands[0].book_extend_ply != 10 || commands[0].eval_limit != 500
            || commands[0].max_book_ply != 200)
            throw std::runtime_error("duplicate position defaults were not applied before merging");
        {
            std::ofstream out(input, std::ios::binary);
            out << "\xef\xbb\xbf  # comment\n  \nstartpos\n";
        }
        const auto bom_commands = read_position_commands_file(input, 400, 200, 10);
        if (bom_commands.size() != 1 || bom_commands[0].position_command != "startpos")
            throw std::runtime_error("BOM comment or blank line was not ignored");
        { std::ofstream out(input, std::ios::binary); out << "\xef\xbb\xbf" << "startpos\n"; }
        const auto bom_position = read_position_commands_file(input, 400, 200, 10);
        if (bom_position.size() != 1 || bom_position[0].position_command != "startpos")
            throw std::runtime_error("UTF-8 BOM was retained in the position command");
    }
    {
        const auto dir = roots_path.parent_path();
        const auto output = dir / "merge-preserve.txt";
        const auto black = dir / "merge-black.txt";
        { std::ofstream out(output); out << "existing\n"; }
        { std::ofstream out(black); out << "startpos\n"; }
        bool failed = false;
        try { merge_black_white_think_sfens(black, dir / "missing-white.txt", output); }
        catch (const std::runtime_error&) { failed = true; }
        std::ifstream in(output);
        std::string line;
        std::getline(in, line);
        if (!failed || line != "existing")
            throw std::runtime_error("missing merge input damaged existing output");
#ifndef _WIN32
        const auto temporary = bookminer::temp_book_path(output);
        std::filesystem::create_symlink("/dev/full", temporary);
        failed = false;
        try { write_position_commands_file(output, {"startpos"}); }
        catch (const std::runtime_error&) { failed = true; }
        std::ifstream in2(output);
        std::getline(in2, line);
        if (!failed || line != "existing")
            throw std::runtime_error("failed write damaged existing output or was not reported");
#endif
    }
    auto board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
    bookminer::BookStore book;
    book.merge_position(board.sfen_with_ply(), 1, {
        {bookminer::move16_from_usi("7g7f"), 100, 1},
    });
    board.push_usi("7g7f");
    book.merge_position(board.sfen_with_ply(), 2, {
        {bookminer::move16_from_usi("3c3d"), 0, 1},
        {bookminer::move16_from_usi("8c8d"), -50, 1},
    });
    {
        std::ofstream out(roots_path);
        out << "startpos\n" << "sfen " << board.sfen_with_ply() << '\n';
    }
    const auto leaves = peta_next_for_turn(nullptr, book, 1, 10, 10, 100, roots_path, std::nullopt);
    const auto expected = std::string("startpos moves 7g7f 8c8d");
    if (std::find(leaves.begin(), leaves.end(), expected) == leaves.end())
        throw std::runtime_error("overlapping root suppressed a reachable leaf");
    if (leaves.size() != 2)
        throw std::runtime_error("same leaf was emitted more than once");

    // Side to move is not determined by ply parity for arbitrary root SFENs.
    const std::string white_root = std::string(bookminer::StartSfenPly1).replace(
        std::string(bookminer::StartSfenPly1).find(" b "), 3, " w ");
    bookminer::BookStore white_book;
    white_book.merge_position(white_root, 1, {
        {bookminer::move16_from_usi("3c3d"), 0, 1},
        {bookminer::move16_from_usi("8c8d"), -5, 1},
    });
    {
        std::ofstream out(roots_path, std::ios::binary);
        out << "\xef\xbb\xbf" << "sfen " << white_root << '\n';
    }
    const auto white_leaves = peta_next_for_turn(nullptr, white_book, 1, 10, 10, 100, roots_path, std::nullopt);
    if (white_leaves.size() != 2)
        throw std::runtime_error("SFEN side to move was not honored");

    // Rejoin eligibility can depend on the evaluation of the incoming edge.
    std::unordered_map<PetaVisitKey, int, PetaVisitKeyHash> visited;
    PetaNextNode first{white_root, 0, 10, 10};
    PetaNextNode second{white_root, 0, 10, 20};
    if (!mark_peta_state(visited, white_root, 1, first, true)
        || !mark_peta_state(visited, white_root, 1, second, true)
        || mark_peta_state(visited, white_root, 2, first, true)
        || !mark_peta_state(visited, white_root, 0, first, true))
        throw std::runtime_error("rejoin traversal state was incorrectly deduplicated");

    // Two predecessors reach the same leaf with different incoming scores.
    // Only the second one satisfies the rejoin evaluation-drop condition.
    auto a = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
    for (const auto* move : {"7g7f", "3c3d", "2g2f"}) a.push_usi(move);
    auto b = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
    for (const auto* move : {"2g2f", "8c8d", "7g7f"}) b.push_usi(move);
    bookminer::BookStore rejoin_book;
    rejoin_book.merge_position(a.sfen_with_ply(), 4, {
        {bookminer::move16_from_usi("4c4d"), 100, 1},
        {bookminer::move16_from_usi("8c8d"), -50, 1},
    });
    rejoin_book.merge_position(b.sfen_with_ply(), 4, {
        {bookminer::move16_from_usi("4c4d"), 100, 1},
        {bookminer::move16_from_usi("3c3d"), 20, 1},
    });
    auto rejoined = a;
    rejoined.push_usi("8c8d");
    rejoined.push_usi("2f2e");
    rejoin_book.merge_position(rejoined.sfen_with_ply(), 6, {
        {bookminer::move16_from_usi("4c4d"), 0, 1},
    });
    {
        std::ofstream out(roots_path);
        out << "sfen " << a.sfen_with_ply() << '\n';
        out << "sfen " << b.sfen_with_ply() << '\n';
    }
    PetaRejoinStats stats;
    const auto rejoin_leaves = peta_rejoin_for_turn(rejoin_book, 1, 200, 10, 100, roots_path, 10, stats);
    const auto rejoin_expected = "sfen " + b.sfen_with_ply() + " moves 3c3d";
    if (std::find(rejoin_leaves.begin(), rejoin_leaves.end(), rejoin_expected) == rejoin_leaves.end())
        throw std::runtime_error("rejoin leaf was suppressed despite different incoming score");
}
