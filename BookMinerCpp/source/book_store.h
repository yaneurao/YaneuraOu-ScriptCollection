#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace bookminer {

inline constexpr const char* YaneuraOuBookHeaderV1 = "#YANEURAOU-DB2016 1.00";
inline constexpr int ValueMate = 32000;
inline constexpr int ValueEvalClamp = 30000;
inline constexpr int OldBookValueMate = 100000;
inline constexpr int OldBookMateThreshold = 99000;
inline constexpr std::size_t BookReadProgressInterval = 10000;
inline constexpr std::size_t BookWriteProgressInterval = 10000;
inline constexpr std::size_t YaneBinBookIndexRecordSize = 44;
inline constexpr std::size_t LsmMemtableFlushThreshold = 65536;

struct PackedSfen {
    std::array<std::uint8_t, 32> bytes{};

    friend bool operator==(const PackedSfen& lhs, const PackedSfen& rhs) noexcept
    {
        return lhs.bytes == rhs.bytes;
    }

    static PackedSfen from_sfen(const std::string& sfen);
    PackedSfen flipped() const;
    void flip();
};

struct PackedSfenHash {
    std::size_t operator()(const PackedSfen& key) const noexcept;
};

struct MoveInfo {
    std::uint16_t move16 = 0;
    std::int16_t eval = 0;
    std::uint16_t depth = 0;
};

struct PositionInfo {
    std::uint16_t ply = 0;
    std::vector<MoveInfo> moves;
};

struct BookEntry {
    PackedSfen key;
    PositionInfo position;
};

struct SearchLease {
    bool acquired = false;
    std::string sfen;
    std::string flipped_sfen;
    std::optional<PositionInfo> position;
};

enum class BookProgressKind {
    Start,
    Progress,
    Done,
};

using BookProgressCallback = void (*)(BookProgressKind kind, std::size_t current, std::optional<std::size_t> total, const std::filesystem::path& path, void* user);

class BookStore {
public:
    using Map = std::unordered_map<PackedSfen, PositionInfo, PackedSfenHash>;

    std::size_t size() const;
    bool empty() const;
    void clear();

    PositionInfo* find_position(const std::string& sfen);
    const PositionInfo* find_position(const std::string& sfen) const;
    const PositionInfo* find_position(const PackedSfen& key) const;
    std::optional<PositionInfo> find_position_copy(const std::string& sfen) const;
    std::optional<PositionInfo> find_position_copy(const PackedSfen& key) const;
    SearchLease try_begin_search(const std::string& sfen, const std::string& flipped_sfen);
    void end_search(const SearchLease& lease);
    void merge_position(const std::string& sfen, std::uint16_t ply, const std::vector<MoveInfo>& moves);
    std::size_t count_save_positions(std::optional<int> ply_limit) const;
    std::vector<BookEntry> snapshot_entries() const;

    void load_yaneuraou_book(
        const std::filesystem::path& path,
        bool normalize_eval,
        BookProgressCallback progress,
        void* user);

    std::size_t save_yaneuraou_book(
        const std::filesystem::path& path,
        std::optional<int> ply_limit,
        BookProgressCallback progress,
        void* user) const;

private:
    using Entry = BookEntry;
    using Run = std::vector<Entry>;

    PositionInfo* find_position_locked(const PackedSfen& key);
    const PositionInfo* find_position_locked(const PackedSfen& key) const;
    void flush_memtable_locked();
    void compact_runs_locked();
    std::size_t count_save_positions_locked(std::optional<int> ply_limit) const;
    std::vector<Entry> snapshot_save_entries_locked(std::optional<int> ply_limit) const;

    mutable std::mutex mutex_;
    std::size_t size_ = 0;
    Map memtable_;
    std::vector<Run> runs_;
    std::unordered_set<PackedSfen, PackedSfenHash> searching_;
};

std::uint16_t move16_from_usi(const std::string& usi);
std::string move16_to_usi(std::uint16_t move16);
std::int16_t normalize_book_eval(int eval);
std::string trim_sfen(const std::string& sfen_with_optional_prefix_and_ply);
std::pair<std::string, int> trim_sfen_ply(const std::string& sfen_with_optional_prefix_and_ply);
std::filesystem::path temp_book_path(const std::filesystem::path& path);
bool is_yane_bin_book_path(const std::filesystem::path& path);

} // namespace bookminer
