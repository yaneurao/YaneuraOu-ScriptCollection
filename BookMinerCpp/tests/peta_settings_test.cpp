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
               "output = pathlib.Path('book') / command.split()[3]\n"
               "if pathlib.Path('simulate-error').exists():\n"
               " output.write_text('partial')\n"
               " print('info string Error! : write failed')\n"
               "elif pathlib.Path('simulate-incomplete').exists():\n"
               " output.write_text('partial')\n"
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
        ConcurrentManualBackup manual;
        std::atomic<bool> entered{false}, release{false};
        if (!manual.start([&] {
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
