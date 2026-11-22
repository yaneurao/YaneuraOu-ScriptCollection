#define main bookminer_program_main
#include "../source/main.cpp"
#undef main

int main(int argc, char** argv)
{
#ifdef _WIN32
    (void)argc; (void)argv;
    return 0;
#else
    if (argc != 2) return 2;
    {
        int fds[2];
        if (pipe(fds) != 0) throw std::runtime_error("failed to create test pipe");
        close(fds[0]);
        errno = 0;
        const auto result = write_peta_input_without_sigpipe(fds[1], "x", 1);
        const int error = errno;
        close(fds[1]);
        if (result != -1 || error != EPIPE)
            throw std::runtime_error("closed peta engine pipe was not reported as EPIPE");
    }
    const auto root = std::filesystem::absolute(argv[1]);
    std::filesystem::create_directories(root / "engine directory");
    std::filesystem::create_directories(root / "book" / "backup");
    const auto engine = root / "engine directory" / "custom engine";
    {
        std::ofstream out(engine);
        out << "#!/usr/bin/env python3\n"
               "import pathlib, sys\n"
               "commands = sys.stdin.read()\n"
               "pathlib.Path('commands.txt').write_text(commands)\n"
               "command = next(line for line in commands.splitlines() if line.startswith('makebook '))\n"
               "source = pathlib.Path('book') / command.split()[2]\n"
               "if not source.is_file() or source.suffix not in ('.db', '.ybb'): raise RuntimeError('invalid input')\n"
               "output = pathlib.Path('book') / command.split()[3]\n"
               "if pathlib.Path('simulate-error').exists():\n"
               " output.write_text('partial')\n"
               " print('info string Error! : write failed')\n"
               "elif pathlib.Path('simulate-incomplete').exists():\n"
               " output.write_text('partial')\n"
               "elif pathlib.Path('simulate-empty').exists():\n"
               " output.write_text('')\n"
               " print('Making a peta-shock book has been completed.')\n"
               "else:\n"
               " output.write_text('result')\n"
               " print('Making a peta-shock book has been completed.')\n";
    }
    std::filesystem::permissions(engine, std::filesystem::perms::owner_all);
    const auto source = root / "book" / "backup" / "book_miner-20261029000000_1.db";
    { std::ofstream out(source); out << "source"; }
    const auto old_cwd = std::filesystem::current_path();
    std::filesystem::current_path(root);
    {
        bookminer::BookStore book;
        book.merge_position(bookminer::StartSfenPly1, 1, {{bookminer::move16_from_usi("7g7f"), 10, 0}});
        const auto first = save_book_backup(book, std::nullopt);
        book.merge_position(bookminer::StartSfenPly1, 1, {{bookminer::move16_from_usi("7g7f"), 20, 0}});
        const auto second = save_book_backup(book, std::nullopt);
        if (first == second) throw std::runtime_error("same-second backup overwrote an existing backup");
        bookminer::BookStore first_book, second_book;
        first_book.load_yaneuraou_book(first, false, nullptr, nullptr);
        second_book.load_yaneuraou_book(second, false, nullptr, nullptr);
        if (first_book.find_position_copy(bookminer::StartSfenPly1)->moves[0].eval != 10
            || second_book.find_position_copy(bookminer::StartSfenPly1)->moves[0].eval != 20)
            throw std::runtime_error("backups did not preserve their separate snapshots");
    }
    {
        const auto peta_file = root / "book" / "backup" / "isolation-peta.db";
        { std::ofstream out(peta_file); out << "#YANEURAOU-DB2016 1.00\nsfen "
              << bookminer::StartSfenPly1 << "\n7g7f none 0 0\n"; }
        for (const bool cancel_manual : {true, false})
        {
            bookminer::BookStore peta_book;
            PetaBookReadJob peta(peta_book);
            ConcurrentManualBackup manual;
            std::atomic<bool> peta_entered{false}, manual_entered{false}, peta_release{false}, manual_release{false};
            auto wait_until = [](const std::atomic<bool>& flag) {
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
                while (!flag && std::chrono::steady_clock::now() < deadline)
                    std::this_thread::sleep_for(std::chrono::milliseconds(1));
                return flag.load();
            };
            if (!peta.start_shock([&](const std::atomic<bool>* cancelled) {
                    peta_entered = true;
                    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
                    while (!peta_release) {
                        check_peta_shock_cancel(cancelled);
                        if (std::chrono::steady_clock::now() >= deadline) throw std::runtime_error("peta fixture timed out");
                        std::this_thread::sleep_for(std::chrono::milliseconds(1));
                    }
                    return peta_file;
                }, "test peta done"))
                throw std::runtime_error("could not start peta isolation fixture");
            if (!manual.start([&](const std::atomic<bool>* cancelled) {
                    manual_entered = true;
                    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
                    while (!manual_release) {
                        check_peta_shock_cancel(cancelled);
                        if (std::chrono::steady_clock::now() >= deadline) throw std::runtime_error("manual fixture timed out");
                        std::this_thread::sleep_for(std::chrono::milliseconds(1));
                    }
                    return root / "manual.db";
                }))
                throw std::runtime_error("could not start manual isolation fixture");
            if (!wait_until(peta_entered) || !wait_until(manual_entered)) {
                peta_release = true; manual_release = true;
                peta.join(); manual.join();
                throw std::runtime_error("isolation fixture did not start");
            }
            if (cancel_manual) {
                if (!manual.cancel()) throw std::runtime_error("manual cancellation was ignored");
                manual.join();
                if (!peta.shock_running()) throw std::runtime_error("manual cancellation stopped peta shock");
                peta_release = true;
                peta.join();
                if (peta_book.size() != 1) throw std::runtime_error("peta shock did not complete after manual cancellation");
            } else {
                if (!peta.cancel_shock()) throw std::runtime_error("peta cancellation was ignored");
                peta.join();
                if (!manual.running()) throw std::runtime_error("peta cancellation stopped manual backup");
                manual_release = true;
                manual.join();
            }
        }
    }
    {
        ConcurrentManualBackup manual;
        std::atomic<bool> entered{false}, release{false};
        if (!manual.start([&](const std::atomic<bool>*) {
                entered = true;
                while (!release.load()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
                return root / "manual.db";
            }))
            throw std::runtime_error("manual backup did not start");
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!entered.load() && std::chrono::steady_clock::now() < deadline)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        const bool running = entered.load() && manual.running();
        release = true;
        manual.join();
        if (!running || manual.running())
            throw std::runtime_error("manual backup did not run concurrently");
    }
    {
        ConcurrentManualBackup manual;
        bookminer::BookStore book;
        std::atomic<bool> entered{false};
        g_save_mutex.lock();
        if (!manual.start([&](const std::atomic<bool>* cancelled) {
                entered = true;
                return save_book_backup(book, std::nullopt, cancelled);
            }))
            throw std::runtime_error("manual backup did not start");
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!entered.load() && std::chrono::steady_clock::now() < deadline)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        if (!entered || !manual.cancel())
        {
            g_save_mutex.unlock();
            manual.join();
            throw std::runtime_error("manual backup cancellation was not accepted");
        }
        manual.join();
        g_save_mutex.unlock();
        if (manual.running() || manual.cancel())
            throw std::runtime_error("cancelled manual backup remained active");
    }
    {
        bookminer::BookStore book;
        std::atomic<bool> cancel{false}, interrupted{false};
        g_save_mutex.lock();
        std::thread worker([&] {
            try { (void)save_book_backup(book, std::nullopt, &cancel); }
            catch (const PetaShockCancelled&) { interrupted = true; }
        });
        cancel = true;
        worker.join();
        g_save_mutex.unlock();
        if (!interrupted)
            throw std::runtime_error("cancellation did not interrupt waiting for another backup");
    }
    {
        bookminer::BookStore current, incoming;
        current.merge_position(bookminer::StartSfenPly1, 1, {{bookminer::move16_from_usi("7g7f"), 10, 1}});
        incoming.merge_position(bookminer::StartSfenPly1, 1, {{bookminer::move16_from_usi("2g2f"), 20, 1}});
        auto cancel_before_commit = [](bookminer::BookProgressKind kind, std::size_t,
                                       std::optional<std::size_t>, const fs::path&, void*) {
            if (kind == bookminer::BookProgressKind::Progress) throw PetaShockCancelled{};
        };
        for (const auto* extension : {".db", ".ybb"})
        {
            const auto path = root / "book" / (std::string("read-cancel") + extension);
            incoming.save_yaneuraou_book(path, std::nullopt, nullptr, nullptr);
            int token = 1;
            bool interrupted = false;
            try { current.load_yaneuraou_book(path, false, cancel_before_commit, &token); }
            catch (const PetaShockCancelled&) { interrupted = true; }
            const auto previous = current.find_position_copy(bookminer::StartSfenPly1);
            if (!interrupted || !previous || previous->moves.size() != 1
                || previous->moves.front().move16 != bookminer::move16_from_usi("7g7f"))
                throw std::runtime_error("cancelled peta read changed the previous book");
        }
    }
    bookminer::BookMinerSettings settings;
    const auto unicode_engine = root / std::filesystem::path(u8"エンジン.exe");
    { std::ofstream out(unicode_engine); out << "engine"; }
    settings.peta_shock_engine_path = "エンジン.exe";
    if (find_peta_shock_engine(root, settings) != unicode_engine)
        throw std::runtime_error("UTF-8 engine path was not resolved");
    settings.peta_shock_engine_path = "engine directory/custom engine";
    settings.peta_shock_threads = 4;
    const auto old_temp = root / "book" / "backup" / "tmp-peta_book-20261029000000_1.db";
    { std::ofstream out(old_temp); out << "unrelated"; }
    const auto output = run_peta_shock_makebook(root, source, settings);
    if (!std::filesystem::is_regular_file(output))
        throw std::runtime_error("peta shock output missing");
    { std::ifstream in(old_temp); std::string text; in >> text;
      if (text != "unrelated") throw std::runtime_error("peta shock damaged an unrelated old temporary file"); }
    std::ifstream in(root / "commands.txt");
    const std::string commands((std::istreambuf_iterator<char>(in)), {});
    if (commands.find(" threads 4\n") == std::string::npos)
        throw std::runtime_error("peta shock thread count was not forwarded");
    settings.peta_shock_engine_path = engine.string();
    settings.peta_shock_threads = 1;
    (void)run_peta_shock_makebook(root, source, settings);
    std::ifstream in_default(root / "commands.txt");
    const std::string default_commands((std::istreambuf_iterator<char>(in_default)), {});
    if (default_commands.find(" threads ") != std::string::npos)
        throw std::runtime_error("single-thread default unexpectedly changed the command");
    for (const auto* extension : {".DB", ".YBB"})
    {
        const auto upper_source = root / "book" / "backup" / (std::string("upper") + extension);
        { std::ofstream out(upper_source); out << "source"; }
        (void)run_peta_shock_makebook(root, upper_source, settings);
        std::ifstream commands_in(root / "commands.txt");
        const std::string contents((std::istreambuf_iterator<char>(commands_in)), {});
        const auto begin = contents.find("makebook peta_shock ") + std::string("makebook peta_shock ").size();
        const auto end = contents.find(' ', begin);
        const auto alias = fs::path("book") / contents.substr(begin, end - begin);
        if (alias.extension() != (std::string(extension) == ".DB" ? ".db" : ".ybb") || fs::exists(alias))
            throw std::runtime_error("uppercase source alias was not lowercase or not cleaned up");
        std::ifstream source_in(upper_source);
        std::string source_data; source_in >> source_data;
        if (source_data != "source") throw std::runtime_error("uppercase source was changed");
    }
    {
        const auto upper_source = root / "book" / "backup" / "upper.YBB";
        { std::ofstream marker(root / "simulate-error"); }
        bool alias_failed = false;
        try { (void)run_peta_shock_makebook(root, upper_source, settings); }
        catch (const std::runtime_error&) { alias_failed = true; }
        std::filesystem::remove(root / "simulate-error");
        if (!alias_failed) throw std::runtime_error("failed uppercase peta shock was accepted");
        for (const auto& entry : fs::directory_iterator(root / "book" / "backup"))
            if (entry.path().filename().string().find("peta-source") != std::string::npos)
                throw std::runtime_error("failed peta shock left a source alias");
    }
    { std::ofstream marker(root / "simulate-error"); }
    bool failed = false;
    try { (void)run_peta_shock_makebook(root, source, settings); }
    catch (const std::runtime_error&) { failed = true; }
    std::ifstream preserved(output);
    std::string content;
    preserved >> content;
    if (!failed || content != "result")
        throw std::runtime_error("failed peta shock replaced the previous output");
    std::filesystem::remove(root / "simulate-error");
    {
        const auto previous_count = std::distance(fs::directory_iterator(root / "book" / "backup"), fs::directory_iterator{});
        { std::ofstream marker(root / "simulate-empty"); }
        bool empty_failed = false;
        try { (void)run_peta_shock_makebook(root, source, settings); }
        catch (const std::runtime_error&) { empty_failed = true; }
        std::filesystem::remove(root / "simulate-empty");
        const auto after_count = std::distance(fs::directory_iterator(root / "book" / "backup"), fs::directory_iterator{});
        if (!empty_failed || previous_count != after_count)
            throw std::runtime_error("empty peta output was accepted or left behind");
        std::ifstream existing(output);
        std::string existing_content; existing >> existing_content;
        if (existing_content != "result") throw std::runtime_error("empty peta output replaced the existing book");
    }
    { std::ofstream marker(root / "simulate-incomplete"); }
    failed = false;
    try { (void)run_peta_shock_makebook(root, source, settings); }
    catch (const std::runtime_error&) { failed = true; }
    std::ifstream preserved_again(output);
    preserved_again >> content;
    if (!failed || content != "result")
        throw std::runtime_error("incomplete peta shock replaced the previous output");
    std::filesystem::current_path(old_cwd);
    return 0;
#endif
}
