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
    bookminer::BookMinerSettings settings;
    const auto unicode_engine = root / std::filesystem::path(u8"エンジン.exe");
    { std::ofstream out(unicode_engine); out << "engine"; }
    settings.peta_shock_engine_path = "エンジン.exe";
    if (find_peta_shock_engine(root, settings) != unicode_engine)
        throw std::runtime_error("UTF-8 engine path was not resolved");
    settings.peta_shock_engine_path = "engine directory/custom engine";
    settings.peta_shock_threads = 4;
    const auto output = run_peta_shock_makebook(root, source, settings);
    if (!std::filesystem::is_regular_file(output))
        throw std::runtime_error("peta shock output missing");
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
