#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace bookminer {

struct EngineConfig {
    std::string path;
    std::string name;
    std::uint64_t nodes = 1000000;
    int multi = 1;
    int multipv = 4;
    int multipv_delta = 100;
};

struct BookMinerSettings {
    int auto_save_interval_seconds = 3 * 60 * 60;
    int max_book_ply = 200;
    std::string peta_next_start_sfens_path = "book/peta_start_sfens.txt";
};

std::vector<EngineConfig> load_engine_settings(const std::filesystem::path& path);
BookMinerSettings load_book_miner_settings(const std::vector<std::filesystem::path>& candidate_paths);

} // namespace bookminer
