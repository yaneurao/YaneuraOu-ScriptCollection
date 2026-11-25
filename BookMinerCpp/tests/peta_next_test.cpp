#define main bookminer_program_main
#include "../source/main.cpp"
#undef main

int main(int argc, char** argv)
{
    if (argc != 2)
        return 2;
    {
        if (!is_supported_opponent_book_path("opponent.DB") || !is_supported_opponent_book_path("opponent.YBB")
            || !is_supported_peta_book_path("book.Db") || !is_supported_peta_book_path("book.YbB")
            || is_supported_opponent_book_path("opponent-INDEX.YBB"))
            throw std::runtime_error("book extension matching is not case insensitive");
        const auto parsed = parse_regular_book_backup_name("book_miner-20261120000000_42.YBB");
        if (!parsed || parsed->first != "20261120000000" || parsed->second != 42)
            throw std::runtime_error("uppercase backup extension lost source metadata");
    }
    {
        bookminer::PositionInfo position;
        position.moves = {{bookminer::move16_from_usi("7g7f"), -100, 10},
                          {bookminer::move16_from_usi("2g2f"), -200, 10}};
        const auto moves = candidate_best_moves_from_position(position, false, std::numeric_limits<int>::max());
        if (moves.size() != 2)
            throw std::runtime_error("large opponent eval_diff overflowed and discarded candidate moves");
    }
    {
        bool running_when_notified = true;
        std::string completion;
        PetaCommandJob* job_ptr = nullptr;
        PetaCommandJob job([&](const std::string& message) {
            running_when_notified = job_ptr->running();
            completion = message;
        });
        job_ptr = &job;
        if (!job.start([] { return std::string("[PetaNextDone] test"); }, true))
            throw std::runtime_error("could not start peta command");
        job.join();
        if (running_when_notified || completion != "[PetaNextDone] test")
            throw std::runtime_error("peta completion reported before command became idle");
        if (!job.start([]() -> std::string { throw std::runtime_error("test failure"); }))
            throw std::runtime_error("could not restart peta command");
        job.join();
        if (running_when_notified || completion.find("Exception : peta command failed") != 0)
            throw std::runtime_error("peta failure reported before command became idle");
        if (!job.start([&job] {
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
                while (!job.cancellation_flag()->load() && std::chrono::steady_clock::now() < deadline)
                    std::this_thread::yield();
                check_peta_next_cancel(job.cancellation_flag());
                throw std::runtime_error("unsolved cancellation was not delivered");
                return std::string();
            }, false, true))
            throw std::runtime_error("could not start unsolved cancellation test");
        if (job.cancel_next() || !job.cancel_unsolved())
            throw std::runtime_error("peta next and unsolved cancellations are not independent");
        job.join();
        if (running_when_notified || completion != "[PetaUnsolvedCancelled]")
            throw std::runtime_error("unsolved cancellation did not report completion");
    }
    {
        TaskQueue queue;
        Task first, other;
        first.job_id = 1;
        other.job_id = 2;
        queue.push(first);
        queue.push(first);
        queue.push(other);
        queue.control(1, "pause");
        if (queue.pop()->job_id != 2 || queue.size() != 2)
            throw std::runtime_error("paused job blocked other jobs");
        queue.control(1, "resume");
        if (queue.pop()->job_id != 1)
            throw std::runtime_error("resumed job was not taken");
        if (queue.control(1, "delete") != 1 || queue.size() != 0 || queue.push(first))
            throw std::runtime_error("deleted job accepted pending or new tasks");
    }
    {
        TaskQueue queue;
        Task task;
        task.job_id = 42;
        std::thread producer([&] {
            for (int i = 0; i < 10000; ++i) queue.push(task);
        });
        queue.control(42, "delete");
        producer.join();
        if (queue.size() != 0 || queue.push(task))
            throw std::runtime_error("deleted job accepted a concurrently generated branch");
    }
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
    std::filesystem::remove(roots_path);
    {
        auto board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        board.push_usi("7g7f");
        const auto key = bookminer::PackedSfen::from_sfen(board.sfen());
        const auto input = roots_path.parent_path() / "empty-direct.ybb";
        {
            std::ofstream out(input, std::ios::binary);
            out.write("YANE-BINBOOK-V1\0", 16);
            auto write_u64 = [&](std::uint64_t x) { for (int i = 0; i < 8; ++i) out.put(static_cast<char>(x >> (8 * i))); };
            write_u64(1); write_u64(0);
            out.write(reinterpret_cast<const char*>(key.bytes.data()), key.bytes.size());
            write_u64(0);
            out.put(2); out.put(0); out.put(0); out.put(0);
        }
        bookminer::BookStore normal;
        normal.load_yaneuraou_book(input, false, nullptr, nullptr);
        const auto flipped = bookminer::flipped_sfen(board.sfen());
        normal.merge_position(flipped, 2, {{bookminer::flipped_move16(bookminer::move16_from_usi("3c3d")), 0, 0}});
        const auto hit = find_considered_book_position_with_flip(normal, board.sfen());
        if (!hit.flipped || !has_considered(hit.position))
            throw std::runtime_error("empty direct entry hid explored flipped position");
        const auto peta_hit = find_peta_position_with_flip(normal, board.sfen());
        if (!peta_hit.flipped || peta_hit.position == nullptr || peta_hit.position->moves.empty())
            throw std::runtime_error("empty direct peta entry hid explored flipped position");
        auto lease = normal.try_begin_search(board.sfen(), flipped);
        if (!lease.acquired || lease.sfen != flipped || !has_considered(lease.position))
            throw std::runtime_error("search lease ignored explored flipped position");
        normal.end_search(lease);
        bookminer::PositionInfo root;
        root.moves = {{bookminer::move16_from_usi("7g7f"), 500, 0}};
        if (peta_cut_by_eval_limit(root, false, bookminer::StartSfenPly1, 400, &normal, true))
            throw std::runtime_error("eval_limit ignored flipped explored child behind empty entry");
    }
    {
        bookminer::BookStore peta, normal;
        auto board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        peta.merge_position(board.sfen_with_ply(), 1, {{bookminer::move16_from_usi("7g7f"), 500, 0}});
        { std::ofstream out(roots_path); out << "startpos\n"; }
        auto leaves = peta_next_for_turn(nullptr, peta, 1, 0, 10, 100, roots_path,
                                         std::nullopt, &normal, nullptr, nullptr, 400);
        if (!leaves.empty()) throw std::runtime_error("eval_limit did not cut unexplored best move");
        board.push_usi("7g7f");
        normal.merge_position(board.sfen_with_ply(), 2, {{bookminer::move16_from_usi("3c3d"), 0, 0}});
        leaves = peta_next_for_turn(nullptr, peta, 1, 0, 10, 100, roots_path,
                                    std::nullopt, &normal, nullptr, nullptr, 400);
        if (std::find(leaves.begin(), leaves.end(), "startpos moves 7g7f 3c3d") == leaves.end())
            throw std::runtime_error("eval_limit ignored explored normal-book best child");
        bookminer::PositionInfo normal_root;
        normal_root.moves = {{bookminer::move16_from_usi("7g7f"), -500, 99}};
        if (peta_cut_by_eval_limit(normal_root, false, bookminer::StartSfenPly1, 400, &normal, true))
            throw std::runtime_error("normal-book best child was not detected");
        if (thinking_candidate_moves(normal_root, 400, std::nullopt, normal, bookminer::StartSfenPly1).size() != 1)
            throw std::runtime_error("best-line extension did not follow explored best move");
        bookminer::BookStore empty;
        if (!peta_cut_by_eval_limit(normal_root, false, bookminer::StartSfenPly1, 400, &empty, true))
            throw std::runtime_error("normal-book stored depth was treated as propagated depth");
        if (!thinking_candidate_moves(normal_root, 400, std::nullopt, empty, bookminer::StartSfenPly1).empty())
            throw std::runtime_error("best-line extension did not stop at unexplored best move");
    }
    {
        const auto old_cwd = std::filesystem::current_path();
        const auto directory = roots_path.parent_path() / "unsolved-fixture";
        std::filesystem::create_directories(directory / "book");
        std::filesystem::current_path(directory);
        bookminer::BookStore peta;
        auto board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        peta.merge_position(board.sfen_with_ply(), 1, {
            {bookminer::move16_from_usi("7g7f"), 100, 9},
            {bookminer::move16_from_usi("2g2f"), 90, 8},
            {bookminer::move16_from_usi("6g6f"), 80, 5},
            {bookminer::move16_from_usi("1g1f"), 70, 4},
        });
        board.push_usi("7g7f");
        peta.merge_position(board.sfen_with_ply(), 2, {{bookminer::move16_from_usi("3c3d"), 401, 0}});
        { std::ofstream out("book/think_unsolved_sfens.txt", std::ios::binary); out << "\xef\xbb\xbf# comment\r\nstartpos moves 7g7f 3c3d\r\n"; }
        (void)peta_unsolved(peta, {10, 8, 6, 4}, 200, 9999, std::nullopt, 400, &peta);
        std::ifstream result("book/think_sfens.txt");
        std::string text((std::istreambuf_iterator<char>(result)), {});
        if (text.find("startpos moves 6g6f") == std::string::npos
            || text.find("startpos moves 7g7f") != std::string::npos
            || text.find("startpos moves 2g2f") != std::string::npos
            || text.find("startpos moves 1g1f") != std::string::npos)
            throw std::runtime_error("unsolved depth thresholds or eval cutoff failed");
        result.close();
        bookminer::BookStore second;
        auto root_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        second.merge_position(root_board.sfen_with_ply(), 1, {
            {bookminer::move16_from_usi("7g7f"), 501, 1},
            {bookminer::move16_from_usi("2g2f"), 0, 0},
        });
        root_board.push_usi("7g7f");
        second.merge_position(root_board.sfen_with_ply(), 2, {{bookminer::move16_from_usi("3c3d"), -401, 0}});
        (void)peta_unsolved(second, {2, 1}, 200, 9999, std::nullopt, 400, &second);
        std::ifstream second_result("book/think_sfens.txt");
        const std::string second_text((std::istreambuf_iterator<char>(second_result)), {});
        if (!second_text.empty())
            throw std::runtime_error("unsolved game cutoff ignored normal eval at positive peta depth");
        const auto second_root = second.find_position_copy(bookminer::StartSfenPly1);
        if (!second_root || unsolved_normal_cut_by_eval_limit(bookminer::StartSfen, &*second_root, 400, &second))
            throw std::runtime_error("unsolved PV cutoff ignored positive peta depth");
        second_result.close();
        bookminer::BookStore seventh;
        seventh.merge_position(bookminer::StartSfenPly1, 1, {
            {bookminer::move16_from_usi("7g7f"), 100, 10},
            {bookminer::move16_from_usi("2g2f"), 99, 10},
            {bookminer::move16_from_usi("6g6f"), 98, 10},
            {bookminer::move16_from_usi("1g1f"), 97, 10},
            {bookminer::move16_from_usi("3g3f"), 96, 0},
            {bookminer::move16_from_usi("4g4f"), 95, 0},
            {bookminer::move16_from_usi("9g9f"), 94, 0},
        });
        { std::ofstream out("book/think_unsolved_sfens.txt"); out << "startpos moves 9g9f 9c9d\n"; }
        (void)peta_unsolved(seventh, {10, 8, 6, 4}, 200, 9999, 20, 400);
        std::ifstream seventh_result("book/think_sfens.txt");
        const std::string seventh_text((std::istreambuf_iterator<char>(seventh_result)), {});
        if (seventh_text.find("startpos moves 9g9f,") == std::string::npos
            || seventh_text.find("startpos moves 9g9f 9c9d,") == std::string::npos
            || seventh_text.find("book_extend_ply=20") == std::string::npos)
            throw std::runtime_error("unsolved skipped game positions after the seventh-ranked move");
        seventh_result.close();
        auto seventh_child = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        seventh_child.push_usi("9g9f");
        seventh.merge_position(seventh_child.sfen_with_ply(), 2, {
            {bookminer::move16_from_usi("9c9d"), 30, 0}});
        { std::ofstream out("book/think_unsolved_sfens.txt"); out << "startpos moves 9g9f\n"; }
        (void)peta_unsolved(seventh, {10, 8, 6, 4}, 200, 9999, 20, 400);
        std::ifstream registered_result("book/think_sfens.txt");
        const std::string registered_text((std::istreambuf_iterator<char>(registered_result)), {});
        if (registered_text.find("startpos moves 9g9f 9c9d,") == std::string::npos)
            throw std::runtime_error("unsolved skipped the seventh game move with a registered child");
        registered_result.close();
        bookminer::BookStore repeated_peta;
        { std::ofstream out("book/think_unsolved_sfens.txt"); out << "startpos moves 5i5h 5a5b 5h5i 5b5a 2g2f\n"; }
        (void)peta_unsolved(repeated_peta, {10}, 200, 9999, 1, 400);
        std::ifstream repeated_result("book/think_sfens.txt");
        const std::string repeated_text((std::istreambuf_iterator<char>(repeated_result)), {});
        auto repeated_root = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        for (const auto* move : {"5i5h", "5a5b", "5h5i", "5b5a"}) repeated_root.push_usi(move);
        const std::string reachable = "sfen " + repeated_root.sfen_with_ply() + " moves 2g2f,";
        if (repeated_text.find(reachable) == std::string::npos)
            throw std::runtime_error("unsolved emitted an unreachable path after repetition");
        repeated_result.close();
        // Ranked PVs reach their leaves at the depths recorded by peta shock.
        bookminer::BookStore bounded;
        const std::vector<std::string> roots = {"7g7f", "2g2f", "6g6f", "1g1f"};
        std::vector<bookminer::MoveInfo> root_moves;
        const std::vector<int> bounds = {10, 8, 6, 4};
        for (std::size_t j = 0; j < roots.size(); ++j)
            root_moves.push_back({bookminer::move16_from_usi(roots[j]), static_cast<std::int16_t>(100 - j),
                                  static_cast<std::uint16_t>(bounds[j] - 1)});
        bounded.merge_position(bookminer::StartSfenPly1, 1, root_moves);
        for (std::size_t root_index = 0; root_index < roots.size(); ++root_index)
        {
            const auto& root_move = roots[root_index];
            auto b = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
            b.push_usi(root_move);
            int remaining = bounds[root_index] - 1;
            for (const auto* move : {"3c3d", "3g3f", "8c8d", "4g4f", "8d8e", "5g5f", "9c9d", "9g9f", "1c1d", "4i3h", "7c7d"})
            {
                if (remaining-- == 0) break;
                bounded.merge_position(b.sfen_with_ply(), static_cast<std::uint16_t>(b.ply()),
                                       {{bookminer::move16_from_usi(move), 0, 0}});
                b.push_usi(move);
            }
        }
        { std::ofstream out("book/think_unsolved_sfens.txt"); out << "startpos\n"; }
        (void)peta_unsolved(bounded, {10, 8, 6, 4}, 200, 99999, 1, 400);
        std::ifstream bounded_result("book/think_sfens.txt");
        std::string bounded_line;
        const std::vector<std::size_t> expected_lengths = {10, 8, 6, 4};
        std::size_t bounded_index = 0;
        while (std::getline(bounded_result, bounded_line))
        {
            const auto entry = parse_position_command_entry(bounded_line);
            const auto parsed = bookminer::parse_position_command(entry.position_command);
            if (bounded_index >= expected_lengths.size() || parsed.moves.size() != expected_lengths[bounded_index++])
                throw std::runtime_error("unsolved did not reach the expected ranked PV leaf");
        }
        if (bounded_index != expected_lengths.size())
            throw std::runtime_error("unsolved lost a ranked depth-limited PV");
        bounded_result.close();

        bookminer::BookStore enough_depth;
        enough_depth.merge_position(bookminer::StartSfenPly1, 1, {{bookminer::move16_from_usi("7g7f"), 0, 11}});
        { std::ofstream out("book/think_unsolved_sfens.txt"); out << "startpos moves 7g7f\n"; }
        const auto enough_summary = peta_unsolved(enough_depth, {10}, 200, 99999, 1, 400);
        if (enough_summary.find("pv_walks=0") == std::string::npos)
            throw std::runtime_error("unsolved followed a game move whose peta depth was already sufficient");

        // A long normal-DB operation may hold its mutex. Cancellation must not wait for it.
        bookminer::BookStore busy_normal;
        std::mt19937 lock_rng(127);
        auto lock_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        int lock_ply = 0;
        while (busy_normal.size() < bookminer::BookWriteProgressInterval + 1)
        {
            auto moves = lock_board.legal_moves();
            if (moves.empty() || lock_ply >= 140)
            {
                lock_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
                lock_ply = 0;
                continue;
            }
            const auto& move = moves[lock_rng() % moves.size()];
            busy_normal.merge_position(lock_board.sfen_with_ply(), static_cast<std::uint16_t>(lock_board.ply()),
                                       {{bookminer::move16_from_usi(move), 0, 0}});
            lock_board.push_usi(move);
            ++lock_ply;
        }
        struct LockTestState { std::atomic<bool> locked{false}, release{false}; } lock_state;
        std::atomic<bool> save_failed{false};
        const auto lock_file = directory / "normal-lock-test.db";
        auto save_progress = +[](bookminer::BookProgressKind kind, std::size_t, std::optional<std::size_t>,
                                 const fs::path&, void* user) {
            auto& state = *static_cast<LockTestState*>(user);
            if (kind == bookminer::BookProgressKind::Progress && !state.locked.exchange(true))
                while (!state.release.load()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        };
        std::thread saver([&] {
            try { busy_normal.save_yaneuraou_book(lock_file, std::nullopt, save_progress, &lock_state); }
            catch (...) { save_failed = true; }
        });
        auto wait_flag = [](const std::atomic<bool>& flag) {
            const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
            while (!flag.load() && std::chrono::steady_clock::now() < deadline)
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            return flag.load();
        };
        const bool save_locked = wait_flag(lock_state.locked);
        std::atomic<bool> normal_cancel{false}, lookup_waiting{false}, lookup_done{false}, lookup_cancelled{false};
        std::thread lookup([&] {
            try {
                (void)unsolved_normal_cut_by_eval_limit(bookminer::StartSfen, nullptr, 400, &busy_normal,
                                                        &normal_cancel, [&](int) { lookup_waiting = true; });
            } catch (const PetaNextCancelled&) { lookup_cancelled = true; }
            lookup_done = true;
        });
        const bool observed_wait = wait_flag(lookup_waiting);
        normal_cancel = true;
        const bool cancelled_before_unlock = wait_flag(lookup_done);
        lock_state.release = true;
        lookup.join();
        saver.join();
        std::filesystem::remove(lock_file);
        if (!save_locked || save_failed || !observed_wait || !cancelled_before_unlock || !lookup_cancelled)
            throw std::runtime_error("unsolved could not cancel while normal book was locked");

        bookminer::BookStore tied;
        auto tied_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        tied.merge_position(tied_board.sfen_with_ply(), 1, {{bookminer::move16_from_usi("7g7f"), 0, 1}});
        tied_board.push_usi("7g7f");
        tied.merge_position(tied_board.sfen_with_ply(), 2, {
            {bookminer::move16_from_usi("3c3d"), -10, 5},
            {bookminer::move16_from_usi("8c8d"), -10, 0}});
        { std::ofstream out("book/think_unsolved_sfens.txt"); out << "startpos\n"; }
        (void)peta_unsolved(tied, {10}, 200, 99999, 1, 400);
        std::ifstream tied_result("book/think_sfens.txt");
        const std::string tied_text((std::istreambuf_iterator<char>(tied_result)), {});
        if (tied_text.find("startpos moves 7g7f 8c8d,") == std::string::npos
            || tied_text.find("startpos moves 7g7f 3c3d,") != std::string::npos)
            throw std::runtime_error("unsolved did not choose the shortest equal-score best PV");
        tied_result.close();
        bookminer::BookStore tied_root;
        tied_root.merge_position(bookminer::StartSfenPly1, 1, {
            {bookminer::move16_from_usi("7g7f"), 100, 11},
            {bookminer::move16_from_usi("2g2f"), 100, 0}});
        (void)peta_unsolved(tied_root, {10}, 200, 99999, 1, 400);
        std::ifstream tied_root_result("book/think_sfens.txt");
        const std::string tied_root_text((std::istreambuf_iterator<char>(tied_root_result)), {});
        if (tied_root_text.find("startpos moves 2g2f,") == std::string::npos
            || tied_root_text.find("startpos moves 7g7f,") != std::string::npos)
            throw std::runtime_error("unsolved ranked equal-score moves by the wrong depth");
        tied_root_result.close();

        bookminer::BookStore tied_normal;
        tied_normal.merge_position(bookminer::StartSfenPly1, 1, {{bookminer::move16_from_usi("7g7f"), 401, 20}});
        const bookminer::PositionInfo tied_cutoff{1, {
            {bookminer::move16_from_usi("7g7f"), 100, 11},
            {bookminer::move16_from_usi("2g2f"), 100, 0}}};
        if (!unsolved_normal_cut_by_eval_limit(bookminer::StartSfen, &tied_cutoff, 400, &tied_normal))
            throw std::runtime_error("unsolved eval cutoff ignored the shortest equal-score best move");

        bookminer::BookStore sufficient_line;
        auto sufficient_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        for (const auto* move : {"7g7f", "3c3d", "2g2f", "8c8d", "2f2e"})
        {
            sufficient_line.merge_position(sufficient_board.sfen_with_ply(), static_cast<std::uint16_t>(sufficient_board.ply()),
                                           {{bookminer::move16_from_usi(move), 0, 11}});
            sufficient_board.push_usi(move);
        }
        { std::ofstream out("book/think_unsolved_sfens.txt"); out << "startpos moves 7g7f 3c3d 2g2f 8c8d\n"; }
        const auto sufficient_summary = peta_unsolved(sufficient_line, {10}, 200, 99999, 1, 400);
        if (sufficient_summary.find("processed_prefixes=5") == std::string::npos
            || sufficient_summary.find("pv_walks=0") == std::string::npos)
            throw std::runtime_error("unsolved failed to visit a game line without redundant PV walks");

        bookminer::BookStore game_peta, game_normal;
        auto game_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        game_peta.merge_position(game_board.sfen_with_ply(), 1, {{bookminer::move16_from_usi("7g7f"), 0, 11}});
        game_normal.merge_position(game_board.sfen_with_ply(), 1, {{bookminer::move16_from_usi("7g7f"), 0, 20}});
        game_board.push_usi("7g7f");
        game_peta.merge_position(game_board.sfen_with_ply(), 2, {{bookminer::move16_from_usi("3c3d"), 0, 11}});
        game_normal.merge_position(game_board.sfen_with_ply(), 2, {{bookminer::move16_from_usi("3c3d"), -401, 20}});
        { std::ofstream out("book/think_unsolved_sfens.txt");
          out << "startpos moves 7g7f 7g7f\nstartpos moves 2g2f\n"; }
        const auto game_cutoff = peta_unsolved(game_peta, {10}, 200, 99999, 1, 400, &game_normal);
        std::ifstream game_cutoff_result("book/think_sfens.txt");
        const std::string game_cutoff_text((std::istreambuf_iterator<char>(game_cutoff_result)), {});
        if (game_cutoff.find("input_games=2 processed_prefixes=4") == std::string::npos
            || game_cutoff_text.find("startpos moves 2g2f,") == std::string::npos
            || game_cutoff_text.find("startpos moves 7g7f") != std::string::npos)
            throw std::runtime_error("unsolved did not stop the current game at eval limit");
        game_cutoff_result.close();

        bookminer::BookStore early_cutoff;
        early_cutoff.merge_position(bookminer::StartSfenPly1, 1, {
            {bookminer::move16_from_usi("7g7f"), 500, 0}});
        { std::ofstream out("book/think_unsolved_sfens.txt"); out << "startpos moves 7g7f 7g7f\n"; }
        (void)peta_unsolved(early_cutoff, {10, 8, 6, 4}, 200, 9999, 1, 400, &early_cutoff);
        std::ifstream cutoff_result("book/think_sfens.txt");
        const std::string cutoff_text((std::istreambuf_iterator<char>(cutoff_result)), {});
        if (!cutoff_text.empty())
            throw std::runtime_error("unsolved did not stop before invalid moves after eval cutoff");
        cutoff_result.close();
        bookminer::BookStore empty;
        (void)peta_unsolved(empty, {10, 8, 6, 4}, 2, 9999, 1, 400);
        std::ifstream ply_result("book/think_sfens.txt");
        const std::string ply_text((std::istreambuf_iterator<char>(ply_result)), {});
        if (ply_text.rfind("startpos,", 0) != 0)
            throw std::runtime_error("unsolved did not stop before invalid moves after max book ply");
        ply_result.close();
        bookminer::BookStore cycle;
        auto cycle_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        for (const auto* move : {"5i5h", "5a5b", "5h5i", "5b5a"})
        {
            cycle.merge_position(cycle_board.sfen_with_ply(), static_cast<std::uint16_t>(cycle_board.ply()),
                                 {{bookminer::move16_from_usi(move), 0, 1}});
            cycle_board.push_usi(move);
        }
        int cycle_steps = 0;
        if (peta_pv_leaf_position_command(cycle, "startpos", bookminer::StartSfenPly1,
                                         bookminer::move16_from_usi("5i5h"), 200, 99999,
                                         std::nullopt, nullptr, nullptr, [&](int) { ++cycle_steps; })
            || cycle_steps > 5)
            throw std::runtime_error("unsolved PV cycle did not terminate");
        std::atomic<bool> cancel_pv{false};
        bool pv_interrupted = false;
        try {
            (void)peta_pv_leaf_position_command(cycle, "startpos", bookminer::StartSfenPly1,
                bookminer::move16_from_usi("5i5h"), 200, 99999, std::nullopt, nullptr, &cancel_pv,
                [&](int step) { if (step >= 2) cancel_pv = true; });
        } catch (const PetaNextCancelled&) { pv_interrupted = true; }
        if (!pv_interrupted) throw std::runtime_error("unsolved did not cancel inside a PV");
        { std::ofstream out("book/think_unsolved_sfens.txt"); out << "startpos\n"; }
        const auto cycle_summary = peta_unsolved(cycle, {10}, 200, 99999, 1, 400);
        if (cycle_summary.find("cycles=1") == std::string::npos)
            throw std::runtime_error("unsolved did not report the cyclic PV");
        std::atomic<bool> cancel_rebase{false};
        bool rebase_interrupted = false;
        try {
            (void)unsolved_rebase_repeated_path("startpos moves 5i5h 5a5b 5h5i 5b5a", &cancel_rebase,
                                                [&](int) { cancel_rebase = true; });
        } catch (const PetaNextCancelled&) { rebase_interrupted = true; }
        if (!rebase_interrupted) throw std::runtime_error("unsolved did not cancel while processing a game path");
        { std::ofstream out("book/think_sfens.txt"); out << "previous\n"; }
        std::atomic<bool> cancel_unsolved{true};
        bool unsolved_interrupted = false;
        try { (void)peta_unsolved(cycle, {10}, 200, 9999, 1, 400, nullptr, &cancel_unsolved); }
        catch (const PetaNextCancelled&) { unsolved_interrupted = true; }
        std::ifstream interrupted_result("book/think_sfens.txt");
        const std::string interrupted_text((std::istreambuf_iterator<char>(interrupted_result)), {});
        if (!unsolved_interrupted || interrupted_text != "previous\n")
            throw std::runtime_error("unsolved cancellation damaged existing output");
        interrupted_result.close();
        bookminer::BookStore pv;
        auto pv_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        pv.merge_position(pv_board.sfen_with_ply(), 1, {{bookminer::move16_from_usi("7g7f"), 20, 2}});
        pv_board.push_usi("7g7f");
        pv.merge_position(pv_board.sfen_with_ply(), 2, {{bookminer::move16_from_usi("3c3d"), 20, 1}});
        pv_board.push_usi("3c3d");
        pv.merge_position(pv_board.sfen_with_ply(), 3, {{bookminer::move16_from_usi("2g2f"), 20, 0}});
        pv_board.push_usi("2g2f");
        pv.merge_position(pv_board.sfen_with_ply(), 4, {{bookminer::move16_from_usi("8c8d"), 20, 0}});
        { std::ofstream out("book/think_unsolved_sfens.txt"); out << "startpos\n"; }
        (void)peta_unsolved(pv, {10, 8, 6, 4}, 200, 9999, 1, 400);
        std::ifstream pv_result("book/think_sfens.txt");
        const std::string pv_text((std::istreambuf_iterator<char>(pv_result)), {});
        if (pv_text.find("startpos moves 7g7f 3c3d 2g2f 8c8d,") == std::string::npos)
            throw std::runtime_error("unsolved did not follow depth-zero best moves to the missing position");
        pv_result.close();
        bookminer::BookStore stale, normal;
        auto normal_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        stale.merge_position(normal_board.sfen_with_ply(), 1, {{bookminer::move16_from_usi("7g7f"), 500, 0}});
        for (const auto* move : {"7g7f", "3c3d", "2g2f"})
        {
            normal.merge_position(normal_board.sfen_with_ply(), static_cast<std::uint16_t>(normal_board.ply()),
                                  {{bookminer::move16_from_usi(move), 0, 99}});
            normal_board.push_usi(move);
        }
        (void)peta_unsolved(stale, {1}, 200, 9999, 1, 400, &normal);
        std::ifstream fallback_result("book/think_sfens.txt");
        const std::string fallback_text((std::istreambuf_iterator<char>(fallback_result)), {});
        if (fallback_text.rfind("startpos moves 7g7f,", 0) != 0)
            throw std::runtime_error("unsolved followed normal book instead of stopping at peta leaf");
        fallback_result.close();
        bookminer::BookStore high_normal;
        high_normal.merge_position(bookminer::StartSfenPly1, 1, {{bookminer::move16_from_usi("7g7f"), 401, 0}});
        (void)peta_unsolved(stale, {1}, 200, 9999, 1, 400, &high_normal);
        std::ifstream high_result("book/think_sfens.txt");
        const std::string high_text((std::istreambuf_iterator<char>(high_result)), {});
        if (!high_text.empty())
            throw std::runtime_error("unsolved did not use normal book evaluation for cutoff");
        high_result.close();
        std::filesystem::remove("book/think_unsolved_sfens.txt");
        std::filesystem::create_directory("book/think_unsolved_sfens.txt");
        { std::ofstream out("book/think_sfens.txt"); out << "previous\n"; }
        bool read_failed = false;
        try { (void)peta_unsolved(pv, {10, 8, 6, 4}, 200, 9999, 1, 400); }
        catch (const std::exception&) { read_failed = true; }
        std::ifstream preserved_result("book/think_sfens.txt");
        const std::string preserved_text((std::istreambuf_iterator<char>(preserved_result)), {});
        if (!read_failed || preserved_text != "previous\n")
            throw std::runtime_error("unsolved replaced output after an input read error");
        preserved_result.close();
        std::filesystem::remove("book/think_unsolved_sfens.txt");
        std::filesystem::current_path(old_cwd);
    }
    {
        const std::string moves = "5i5h 5a5b 5h5i 5b5a 2g2f 8c8d 2f2e 8d8e 7g7f 4a3b 8h7g "
                                  "3c3d 7i6h 2b7g+ 6h7g 3a2b 3i3h 2b3c 6i7h 7a6b 9g9f "
                                  "9c9d 1g1f 1c1d 4g4f 6c6d 3g3f 6a5b";
        { std::ofstream out(roots_path, std::ios::binary);
          out << "\xef\xbb\xbf// comment,xyz\nstartpos moves " << moves << ",100 // comment\n"
                 "startpos moves 7g7f // default\n# comment\n"; }
        const auto roots = load_peta_next_root_positions(roots_path);
        if (roots.size() != 2 || roots[0].position_command != "startpos moves " + moves
            || roots[0].eval_diff != 100 || roots[1].position_command != "startpos moves 7g7f"
            || roots[1].eval_diff.has_value())
            throw std::runtime_error("start position comments were parsed incorrectly");
        { std::ofstream out(roots_path); out << "startpos, invalid // comment\n"; }
        bool rejected = false;
        try { (void)load_peta_next_root_positions(roots_path); }
        catch (const std::runtime_error&) { rejected = true; }
        if (!rejected)
            throw std::runtime_error("invalid eval_diff before comment was accepted");
        std::filesystem::remove(roots_path);
    }
    {
        const std::string moves = "5i5h 5a5b 5h5i 5b5a 2g2f 8c8d 2f2e 8d8e 7g7f 4a3b 8h7g 6a5b";
        auto board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        for (const auto& move : split_ws(moves)) board.push_usi(move);
        const auto legal = board.legal_moves();
        bookminer::BookStore normal, empty;
        normal.merge_position(board.sfen(), 13, {
            {bookminer::move16_from_usi(legal[0]), 100, 0},
            {bookminer::move16_from_usi(legal[1]), 97, 0},
        });
        { std::ofstream out(roots_path); out << "startpos moves " << moves << ",0\n"; }
        const auto zero = peta_next_for_turn(nullptr, empty, 0, 5, 10, 200, roots_path, std::nullopt, &normal);
        { std::ofstream out(roots_path); out << "startpos moves " << moves << "\n"; }
        const auto five = peta_next_for_turn(nullptr, empty, 0, 5, 10, 200, roots_path, std::nullopt, &normal);
        if (zero.size() != 1 || five.size() != 2)
            throw std::runtime_error("per-root zero eval_diff did not override the default of five");
        bookminer::BookStore unpropagated;
        unpropagated.merge_position(board.sfen(), 13, {{bookminer::move16_from_usi(legal[0]), 100, 0}});
        auto child = board;
        child.push_usi(legal[0]);
        const auto replies = child.legal_moves();
        unpropagated.merge_position(child.sfen(), 14, {
            {bookminer::move16_from_usi(replies[0]), 0, 0},
            {bookminer::move16_from_usi(replies[1]), -10, 0},
            {bookminer::move16_from_usi(replies[2]), -20, 0},
        });
        { std::ofstream out(roots_path); out << "startpos moves " << moves << ",0\n"; }
        std::ostringstream log;
        auto* original = std::cout.rdbuf(log.rdbuf());
        const auto expanded = peta_next_for_turn(nullptr, empty, 1, 5, 10, 200, roots_path, std::nullopt, &unpropagated);
        std::cout.rdbuf(original);
        if (expanded.size() != 3 || log.str().find("fallback_nonbest=2") == std::string::npos)
            throw std::runtime_error("unpropagated normal scores were not reported accurately");
        std::filesystem::remove(roots_path);
    }
    {
        bool rejected = false;
        try { (void)load_peta_next_root_positions(roots_path.parent_path()); }
        catch (const std::runtime_error&) { rejected = true; }
        if (!rejected)
            throw std::runtime_error("directory start path was silently treated as a missing file");
    }
    {
        // The normal book extends the old peta frontier, without changing legacy pn.
        bookminer::BookStore peta, normal;
        const auto board0 = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        auto board1 = board0;
        board1.push_usi("7g7f");
        peta.merge_position(board0.sfen(), 1, {{bookminer::move16_from_usi("7g7f"), 0, 0}});
        normal.merge_position(board1.sfen(), 2, {{bookminer::move16_from_usi("3c3d"), 0, 0}});
        const auto legacy = peta_next_for_turn(nullptr, peta, 1, 100, 100, 200, roots_path, std::nullopt);
        const auto extended = peta_next_for_turn(nullptr, peta, 1, 100, 100, 200, roots_path, std::nullopt, &normal);
        if (legacy != std::vector<std::string>{"startpos moves 7g7f"}
            || extended != std::vector<std::string>{"startpos moves 7g7f 3c3d"})
            throw std::runtime_error("peta next did not extend normal book frontier");

        // When both books have the root, the peta book must remain authoritative.
        normal.merge_position(board0.sfen(), 1, {{bookminer::move16_from_usi("2g2f"), 100, 0}});
        const auto priority = peta_next_for_turn(nullptr, peta, 1, 100, 100, 200, roots_path, std::nullopt, &normal);
        if (priority != extended)
            throw std::runtime_error("normal book overrode existing peta position");

        // A flipped normal-book hit must produce moves in the original orientation.
        bookminer::BookStore flipped_normal;
        flipped_normal.merge_position(bookminer::flipped_sfen(board1.sfen()), 2, {
            {bookminer::move16_from_usi("7g7f"), 0, 0},
        });
        const auto flipped_leaves = peta_next_for_turn(nullptr, peta, 1, 100, 100, 200, roots_path, std::nullopt, &flipped_normal);
        if (flipped_leaves != extended)
            throw std::runtime_error("flipped normal book hit was not restored");

        // A root missing from peta may be supplied by the current normal book.
        bookminer::BookStore empty;
        const auto normal_root = peta_next_for_turn(nullptr, empty, 1, 100, 100, 200, roots_path, std::nullopt, &normal);
        if (normal_root != std::vector<std::string>{"startpos moves 2g2f"})
            throw std::runtime_error("peta next did not use normal book root");
    }
    {
        std::ofstream out(roots_path);
        out << "startpos moves 7g7f 3c3d 2g2f 8c8d\n"
               "startpos moves 2g2f 8c8d 7g7f 3c3d\n";
        out.close();
        bookminer::BookStore empty;
        std::ostringstream logs;
        auto* original = std::cout.rdbuf(logs.rdbuf());
        const auto leaves = peta_next_for_turn(nullptr, empty, 1, 50, 100, 200, roots_path, std::nullopt);
        std::cout.rdbuf(original);
        if (leaves.size() != 1 || logs.str().find("roots=2 missing_roots=2") == std::string::npos
            || logs.str().find("duplicate_leaves=1") == std::string::npos)
            throw std::runtime_error("peta next duplicate-root statistics are incorrect");
    }
    {
        // Widening eval_diff must include the second move on the opponent's turn.
        bookminer::BookStore book;
        book.merge_position(bookminer::StartSfenPly1, 1, {
            {bookminer::move16_from_usi("7g7f"), 100, 0},
            {bookminer::move16_from_usi("2g2f"), 60, 0},
        });
        std::filesystem::remove(roots_path);
        const auto narrow = peta_next_for_turn(nullptr, book, 0, 10, 100, 200, roots_path, std::nullopt);
        const auto wide = peta_next_for_turn(nullptr, book, 0, 50, 100, 200, roots_path, std::nullopt);
        if (narrow.size() != 1 || wide.size() != 2
            || std::find(wide.begin(), wide.end(), narrow.front()) == wide.end())
            throw std::runtime_error("peta next lost a leaf when eval_diff increased");
        { std::ofstream out(roots_path); out << "startpos, 50\nstartpos, 0\n"; }
        const auto overridden = peta_next_for_turn(nullptr, book, 0, 10, 100, 200, roots_path, std::nullopt);
        if (overridden.size() != 2)
            throw std::runtime_error("per-root eval_diff did not override the default");
        { std::ofstream out(roots_path); out << "startpos, 0\nsfen " << bookminer::StartSfenPly1 << ", 50\n"; }
        const auto overlapping = peta_next_for_turn(nullptr, book, 0, 10, 100, 200, roots_path, std::nullopt);
        if (overlapping.size() != 2)
            throw std::runtime_error("overlapping roots with different eval_diff lost a branch");
        { std::ofstream out(roots_path); out << "startpos, -1\n"; }
        bool invalid_rejected = false;
        try { (void)load_peta_next_root_positions(roots_path); }
        catch (const std::runtime_error&) { invalid_rejected = true; }
        if (!invalid_rejected)
            throw std::runtime_error("invalid per-root eval_diff was accepted");
        std::filesystem::remove(roots_path);
    }
#ifndef _WIN32
    {
        const auto engine_path = roots_path.parent_path() / "branch-retry-engine";
        {
            std::ofstream out(engine_path);
            out << "#!/usr/bin/env python3\nimport sys\nmove='7g7f'\nalt='3g3f'\n"
                   "line_moves='7g7f 3c3d 2g2f 8c8d 2f2e 8d8e 6g6f 9c9d 1g1f 1c1d 4g4f 4c4d 3g3f 5c5d 9g9f 5a4b 5g5f 7c7d'.split()\n"
                   "for line in sys.stdin:\n"
                   " line=line.strip()\n"
                   " if line=='usi': print('usiok',flush=True)\n"
                   " elif line=='isready': print('readyok',flush=True)\n"
                   " elif line.startswith('position '):\n"
                   "  ply=int(line.split()[-1]); move=line_moves[ply-1]\n"
                   "  alt=('5g5f' if move=='3g3f' else '3g3f') if ply%2 else '7c7d'\n"
                   " elif line.startswith('go '):\n"
                   "  open(__file__+'.nodes','a').write(line+'\\n')\n"
                   "  print('info multipv 1 score cp 42 pv '+move,flush=True)\n"
                   "  print('info multipv 2 score cp 20 pv '+alt,flush=True)\n"
                   "  print('bestmove '+move,flush=True)\n"
                   " elif line=='quit': break\n";
        }
        std::filesystem::permissions(engine_path, std::filesystem::perms::owner_all);
        bookminer::EngineConfig config;
        config.path = engine_path.string();
        config.multipv = 100;
        config.nodes = 100;
        bookminer::UsiEngine engine(0, config, roots_path.parent_path());
        g_consecutive_node_ratio.store(0.45);
        engine.start([](const std::string&) {});
        engine.isready([](const std::string&) {});
        bookminer::BookStore book;
        auto child = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        child.push_usi("7g7f");
        auto lease = book.try_begin_search(child.sfen(), bookminer::flipped_sfen(child.sfen()));
        std::optional<TaskResume> resume;
        auto run = [&] {
            return process_position_command(book, engine, "startpos", 1000, 200, 10,
                                            1, 1, 1, [](BranchTaskSpec) {}, resume);
        };
        if (run() != TaskResult::Deferred || !resume || !resume->best_line
            || !resume->branch_extension || resume->rest_ply != 10)
            throw std::runtime_error("branch search did not suspend at the occupied child");
        book.end_search(lease);
        if (run() != TaskResult::Done || resume || !book.find_position_copy(child.sfen()))
            throw std::runtime_error("branch search did not resume at the suspended child");
        {
            std::ifstream in(engine_path.string() + ".nodes");
            const std::string commands((std::istreambuf_iterator<char>(in)), {});
            if (commands.find("go nodes 45\n") == std::string::npos)
                throw std::runtime_error("configured consecutive node ratio was not used");
        }
        g_consecutive_node_ratio.store(0.8);

        // Explicit game paths must stop at the ply limit even if the DB already has later positions.
        bookminer::BookStore limited_book;
        limited_book.merge_position(bookminer::StartSfenPly1, 1, {{bookminer::move16_from_usi("7g7f"), 0, 1}});
        limited_book.merge_position(child.sfen_with_ply(), 100, {{bookminer::move16_from_usi("3c3d"), 0, 1}});
        std::optional<TaskResume> limited_resume;
        (void)process_position_command(limited_book, engine, "startpos moves 7g7f 3c3d",
                                       1000, 2, 1, 0, 0, 1, [](BranchTaskSpec) {}, limited_resume);
        if (limited_book.find_position_copy(child.sfen())->ply != 100)
            throw std::runtime_error("explicit game path updated a position beyond max book ply");

        bookminer::BookStore repeated_book;
        auto repeated_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        for (const auto* move : {"5i5h", "5a5b", "5h5i", "5b5a"})
        {
            repeated_book.merge_position(repeated_board.sfen_with_ply(), static_cast<std::uint16_t>(repeated_board.ply()),
                                         {{bookminer::move16_from_usi(move), 0, 1}});
            repeated_board.push_usi(move);
        }
        std::optional<TaskResume> repeated_resume;
        (void)process_position_command(repeated_book, engine, "startpos moves 5i5h 5a5b 5h5i 5b5a 2g2f",
                                       1000, 200, 1, 0, 0, 1, [](BranchTaskSpec) {}, repeated_resume);
        repeated_board.push_usi("2g2f");
        if (repeated_book.find_position_copy(repeated_board.sfen()))
            throw std::runtime_error("explicit game path continued beyond a repeated position");

        // A deferred path must retain earlier positions for the repetition check.
        bookminer::BookStore resumed_book;
        auto resumed_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        resumed_board.push_usi("5i5h");
        const auto resumed_child = resumed_board.sfen();
        resumed_board.push_usi("5a5b");
        resumed_book.merge_position(resumed_board.sfen_with_ply(), 3, {{bookminer::move16_from_usi("5h5i"), 0, 1}});
        resumed_board.push_usi("5h5i");
        resumed_book.merge_position(resumed_board.sfen_with_ply(), 4, {{bookminer::move16_from_usi("5b5a"), 0, 1}});
        bookminer::SearchLease resumed_lease;
        std::optional<TaskResume> repeated_path_resume;
        int resumed_branches = 0;
        auto resume_enqueue = [&](BranchTaskSpec) {
            if (++resumed_branches == 1)
                resumed_lease = resumed_book.try_begin_search(resumed_child, bookminer::flipped_sfen(resumed_child));
        };
        auto run_repeated_path = [&] {
            return process_position_command(resumed_book, engine, "startpos moves 5i5h 5a5b 5h5i 5b5a 2g2f",
                                            1000, 200, 1, 2, 2, 1, resume_enqueue, repeated_path_resume);
        };
        if (run_repeated_path() != TaskResult::Deferred || !repeated_path_resume)
            throw std::runtime_error("repeated path did not suspend at the occupied child");
        resumed_book.end_search(resumed_lease);
        auto after_repeat = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        after_repeat.push_usi("2g2f");
        auto beyond_repeat_lease = resumed_book.try_begin_search(after_repeat.sfen(), bookminer::flipped_sfen(after_repeat.sfen()));
        if (run_repeated_path() != TaskResult::Done || repeated_path_resume)
            throw std::runtime_error("repeated path waited for a search beyond the repetition");
        resumed_book.end_search(beyond_repeat_lease);
        if (resumed_book.find_position_copy(after_repeat.sfen()))
            throw std::runtime_error("resumed path lost its repetition history");

        // The best-line extension must remember the exact positions in the game path,
        // including when its first peta leaf is stored in the opposite orientation.
        bookminer::BookStore history_book;
        auto history_board = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        for (const auto& item : std::vector<std::pair<std::string, std::string>>{
                 {"5i5h", "2g2f"}, {"5a5b", "5a5b"}, {"5h5i", "5h5i"}})
        {
            history_book.merge_position(history_board.sfen_with_ply(), static_cast<std::uint16_t>(history_board.ply()),
                                        {{bookminer::move16_from_usi(item.second), 0, 0}});
            history_board.push_usi(item.first);
        }
        history_book.merge_position(bookminer::flipped_sfen(history_board.sfen()), 4,
            {{bookminer::flipped_move16(bookminer::move16_from_usi("5b5a")), 0, 0}});
        const auto nodes_path = std::filesystem::path(engine_path.string() + ".nodes");
        const auto before_history = std::filesystem::file_size(nodes_path);
        std::optional<TaskResume> history_resume;
        (void)process_position_command(history_book, engine, "startpos moves 5i5h 5a5b 5h5i",
                                       1000, 200, 3, 0, 0, 1, [](BranchTaskSpec) {}, history_resume);
        if (std::filesystem::file_size(nodes_path) != before_history)
            throw std::runtime_error("best-line extension continued after returning to a game position");

        auto history_parent = bookminer::SfenPosition::from_sfen(bookminer::StartSfenPly1);
        std::unordered_set<bookminer::PackedSfen, bookminer::PackedSfenHash> history_keys;
        for (const auto* move : {"5i5h", "5a5b", "5h5i"})
        {
            history_keys.insert(bookminer::PackedSfen::from_sfen(history_parent.sfen()));
            history_parent.push_usi(move);
        }
        history_resume = TaskResume{true, "sfen " + history_board.sfen_with_ply(), 3, 0,
                                    PlyMin, engine.thread_id(), {}, false, std::move(history_keys)};
        auto history_lease = history_book.try_begin_search(history_board.sfen(), bookminer::flipped_sfen(history_board.sfen()));
        auto run_history = [&] {
            return process_position_command(history_book, engine, "startpos moves 5i5h 5a5b 5h5i",
                                            1000, 200, 3, 0, 0, 1, [](BranchTaskSpec) {}, history_resume);
        };
        if (run_history() != TaskResult::Deferred || !history_resume || history_resume->path_visited.empty())
            throw std::runtime_error("best-line deferral lost the game path history");
        history_book.end_search(history_lease);
        if (run_history() != TaskResult::Done || std::filesystem::file_size(nodes_path) != before_history)
            throw std::runtime_error("resumed best-line extension lost the game path history");

        bookminer::BookStore path_book;
        std::optional<TaskResume> path_resume;
        bookminer::SearchLease path_lease;
        int branches = 0;
        auto enqueue = [&](BranchTaskSpec) {
            if (++branches == 1)
                path_lease = path_book.try_begin_search(child.sfen(), bookminer::flipped_sfen(child.sfen()));
        };
        auto run_path = [&] {
            return process_position_command(path_book, engine, "startpos moves 7g7f 3c3d",
                                            1000, 200, 10, 2, 2, 1, enqueue, path_resume);
        };
        if (run_path() != TaskResult::Deferred || !path_resume || path_resume->best_line || branches != 1)
            throw std::runtime_error("path search did not suspend after enqueuing its branch");
        path_book.end_search(path_lease);
        if (run_path() != TaskResult::Done || path_resume || branches != 2)
            throw std::runtime_error("path search did not resume without repeating its earlier branch");
    }
#endif
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
        { std::ofstream out(input); out << "startpos\nstartpos moves 7g7f, eval_limt=999\n"; }
        bool rejected = false;
        try { (void)read_position_commands_file(input, 400, 200, 10); }
        catch (const std::exception& error) {
            const std::string message = error.what();
            rejected = message.find(":2") != std::string::npos
                && message.find("unknown metadata: eval_limt") != std::string::npos;
        }
        if (!rejected) throw std::runtime_error("unknown metadata was silently ignored");
    }
    {
        const auto dir = roots_path.parent_path();
        const auto black = dir / "dedup-black.txt";
        const auto white = dir / "dedup-white.txt";
        const auto output = dir / "dedup-output.txt";
        { std::ofstream out(black); out << "startpos, eval_limit=400\nstartpos moves 7g7f\n"; }
        { std::ofstream out(white); out << "startpos, eval_limit=400\nstartpos, eval_limit=500\nstartpos moves 2g2f\n"; }
        if (merge_black_white_think_sfens(black, white, output) != 4)
            throw std::runtime_error("merged position count includes duplicates");
        std::ifstream in(output);
        std::vector<std::string> lines;
        std::string line;
        while (std::getline(in, line)) lines.push_back(line);
        if (lines != std::vector<std::string>{"startpos, eval_limit=400", "startpos moves 7g7f",
                                              "startpos, eval_limit=500", "startpos moves 2g2f"})
            throw std::runtime_error("merged positions lost order or distinct conditions");
    }
    {
        const auto dir = roots_path.parent_path();
        const auto output = dir / "merge-preserve.txt";
        const auto black = dir / "merge-black.txt";
        { std::ofstream out(output); out << "existing\n"; }
        { std::ofstream out(black); out << "startpos\n"; }
        const auto white = dir / "merge-white.txt";
        { std::ofstream out(white); out << "startpos moves 7g7f\n"; }
        std::atomic<bool> cancelled{true};
        bool interrupted = false;
        try { merge_black_white_think_sfens(black, white, output, &cancelled); }
        catch (const PetaNextCancelled&) { interrupted = true; }
        { std::ifstream check(output); std::string old; std::getline(check, old);
          if (!interrupted || old != "existing") throw std::runtime_error("cancellation damaged existing output"); }
        bool failed = false;
        try { merge_black_white_think_sfens(black, dir / "missing-white.txt", output); }
        catch (const std::runtime_error&) { failed = true; }
        std::ifstream in(output);
        std::string line;
        std::getline(in, line);
        if (!failed || line != "existing")
            throw std::runtime_error("missing merge input damaged existing output");
        failed = false;
        try { write_position_file(output, [](std::ostream& out) { out.setstate(std::ios::badbit); }); }
        catch (const std::runtime_error&) { failed = true; }
        std::ifstream in2(output);
        std::getline(in2, line);
        if (!failed || line != "existing")
            throw std::runtime_error("failed write damaged existing output or was not reported");
        in2.close();
        const auto old_temporary = std::filesystem::path(output.string() + ".tmp");
        { std::ofstream out(old_temporary); out << "unrelated"; }
        write_position_commands_file(output, {"startpos"});
        std::ifstream old_in(old_temporary);
        std::getline(old_in, line);
        if (line != "unrelated")
            throw std::runtime_error("position output damaged an unrelated old temporary file");
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
