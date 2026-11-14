#include "config.h"
#include <filesystem>
#include <fstream>
#include <stdexcept>

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    const std::filesystem::path path(argv[1]);
    {
        std::ofstream out(path);
        out << "{}";
    }
    const auto defaults = bookminer::load_book_miner_settings({path});
    if (defaults.peta_shock_engine_path != "YO-MATERIAL.exe" || defaults.peta_shock_threads != 4)
        throw std::runtime_error("peta shock defaults changed");
    {
        std::ofstream out(path);
        out << R"({peta_shock_engine_path: '  dir\\engine.exe  ', peta_shock_threads: 4, peta_next_start_sfens_path: 'book/a//b.txt'})";
    }
    auto settings = bookminer::load_book_miner_settings({path});
    if (settings.peta_shock_engine_path != R"(dir\engine.exe)" || settings.peta_shock_threads != 4
        || settings.peta_next_start_sfens_path != "book/a//b.txt")
        throw std::runtime_error("JSON5 path or thread settings were not read correctly");

    for (const char* value : {"0", "-1", "1.5", "true", "4294967296"})
    {
        { std::ofstream out(path); out << "{peta_shock_threads: " << value << "}"; }
        try { (void)bookminer::load_book_miner_settings({path}); }
        catch (const std::exception&) { continue; }
        throw std::runtime_error(std::string("invalid thread count accepted: ") + value);
    }
    {
        std::ofstream out(path);
        out << "{peta_shock_engine_path: '  '}";
    }
    try
    {
        (void)bookminer::load_book_miner_settings({path});
        throw std::runtime_error("empty peta shock path accepted");
    }
    catch (const std::runtime_error& error)
    {
        if (std::string(error.what()).find("must not be empty") == std::string::npos)
            throw;
    }
    {
        std::ofstream out(path);
        out << R"([{path: 'engine\u0020one', name: 'test//name', nodes: 100}])";
    }
    const auto engines = bookminer::load_engine_settings(path);
    if (engines.size() != 1 || engines[0].path != "engine one" || engines[0].name != "test//name")
        throw std::runtime_error("engine JSON5 strings were not decoded correctly");
}
