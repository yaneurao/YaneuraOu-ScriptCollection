#include "book_store.h"
#include "sfen_position.h"

#include <algorithm>
#include <charconv>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>

namespace bookminer {

namespace {

constexpr std::uint16_t MoveNone = 0;
constexpr std::array<char, 16> YaneBinBookMagic = {
    'Y', 'A', 'N', 'E', '-', 'B', 'I', 'N',
    'B', 'O', 'O', 'K', '-', 'V', '1', '\0',
};
constexpr std::uint64_t YaneBinBookFlagMoveDepth = 1;
constexpr std::uint64_t YaneBinBookKnownFlags = YaneBinBookFlagMoveDepth;
constexpr std::size_t YaneBinBookHeaderSize = 32;
constexpr std::size_t YaneBinBookMoveRecordSize = 4;
constexpr std::size_t YaneBinBookMoveDepthRecordSize = 6;

struct YaneBinBookHeader {
    std::uint64_t record_count = 0;
    std::uint64_t flags = 0;
};

struct YaneBinBookIndexEntry {
    PackedSfen key;
    std::uint64_t moves_offset = 0;
    std::uint16_t ply = 0;
    std::uint16_t move_count = 0;
};

std::vector<std::string> split_ws(const std::string& text)
{
    std::istringstream iss(text);
    std::vector<std::string> tokens;
    std::string token;
    while (iss >> token)
        tokens.push_back(token);
    return tokens;
}

std::vector<std::string> split_char(const std::string& text, char delimiter)
{
    std::vector<std::string> parts;
    std::string part;
    std::istringstream iss(text);
    while (std::getline(iss, part, delimiter))
        parts.push_back(part);
    return parts;
}

bool parse_int(const std::string& text, int& value)
{
    const char* first = text.data();
    const char* last = text.data() + text.size();
    auto [ptr, ec] = std::from_chars(first, last, value);
    return ec == std::errc{} && ptr == last;
}

bool starts_with(const std::string& text, const std::string& prefix)
{
    return text.size() >= prefix.size() && text.compare(0, prefix.size(), prefix) == 0;
}

std::string join_tokens(const std::vector<std::string>& tokens)
{
    std::string out;
    for (const auto& token : tokens)
    {
        if (!out.empty())
            out += ' ';
        out += token;
    }
    return out;
}

struct ParsedMoveLine {
    std::uint16_t move16 = MoveNone;
    std::optional<std::int16_t> eval;
};

ParsedMoveLine parse_book_move_line(const std::string& line, bool normalize_eval)
{
    std::string move_text;
    std::string eval_text;

    if (line.find(',') != std::string::npos)
    {
        const auto parts = split_char(line, ',');
        if (parts.size() < 2)
            throw std::runtime_error("invalid comma move line: " + line);
        move_text = parts[0];
        eval_text = parts[1];
    }
    else
    {
        const auto parts = split_ws(line);
        if (parts.size() < 3)
            throw std::runtime_error("invalid move line: " + line);
        move_text = parts[0];
        eval_text = parts[2];
    }

    ParsedMoveLine parsed;
    parsed.move16 = move16_from_usi(move_text);

    if (eval_text == "None")
        return parsed;

    int eval = 0;
    if (!parse_int(eval_text, eval))
        throw std::runtime_error("invalid eval: " + eval_text);

    parsed.eval = normalize_eval ? normalize_book_eval(eval) : static_cast<std::int16_t>(eval);
    return parsed;
}

void report_progress(BookProgressCallback progress, void* user, BookProgressKind kind, std::size_t current, std::optional<std::size_t> total, const std::filesystem::path& path)
{
    if (progress)
        progress(kind, current, total, path, user);
}

bool has_suffix(const std::string& text, const std::string& suffix)
{
    return text.size() >= suffix.size() && text.compare(text.size() - suffix.size(), suffix.size(), suffix) == 0;
}

void read_exact(std::istream& in, char* data, std::size_t size, const std::filesystem::path& path)
{
    in.read(data, static_cast<std::streamsize>(size));
    if (in.gcount() != static_cast<std::streamsize>(size))
        throw std::runtime_error("failed to read binary book: " + path.string());
}

void write_exact(std::ostream& out, const char* data, std::size_t size, const std::filesystem::path& path)
{
    out.write(data, static_cast<std::streamsize>(size));
    if (!out)
        throw std::runtime_error("failed to write binary book: " + path.string());
}

std::uint16_t read_u16_le(std::istream& in, const std::filesystem::path& path)
{
    std::array<unsigned char, 2> bytes{};
    read_exact(in, reinterpret_cast<char*>(bytes.data()), bytes.size(), path);
    return static_cast<std::uint16_t>(bytes[0] | (bytes[1] << 8));
}

std::uint64_t read_u64_le(std::istream& in, const std::filesystem::path& path)
{
    std::array<unsigned char, 8> bytes{};
    read_exact(in, reinterpret_cast<char*>(bytes.data()), bytes.size(), path);
    std::uint64_t value = 0;
    for (int i = 7; i >= 0; --i)
    {
        value <<= 8;
        value |= bytes[static_cast<std::size_t>(i)];
    }
    return value;
}

void write_u16_le(std::ostream& out, std::uint16_t value, const std::filesystem::path& path)
{
    const std::array<char, 2> bytes = {
        static_cast<char>(value & 0xff),
        static_cast<char>((value >> 8) & 0xff),
    };
    write_exact(out, bytes.data(), bytes.size(), path);
}

void write_i16_le(std::ostream& out, std::int16_t value, const std::filesystem::path& path)
{
    write_u16_le(out, static_cast<std::uint16_t>(value), path);
}

void write_u64_le(std::ostream& out, std::uint64_t value, const std::filesystem::path& path)
{
    std::array<char, 8> bytes{};
    for (std::size_t i = 0; i < bytes.size(); ++i)
    {
        bytes[i] = static_cast<char>(value & 0xff);
        value >>= 8;
    }
    write_exact(out, bytes.data(), bytes.size(), path);
}

YaneBinBookHeader read_ybb_header(std::istream& in, const std::filesystem::path& path)
{
    std::array<char, 16> magic{};
    read_exact(in, magic.data(), magic.size(), path);
    if (magic != YaneBinBookMagic)
        throw std::runtime_error("invalid ybb magic: " + path.string());
    YaneBinBookHeader header;
    header.record_count = read_u64_le(in, path);
    header.flags = read_u64_le(in, path);
    if (header.flags & ~YaneBinBookKnownFlags)
        throw std::runtime_error("unknown ybb flags: " + path.string());
    return header;
}

void write_ybb_header(std::ostream& out, std::uint64_t record_count, std::uint64_t flags, const std::filesystem::path& path)
{
    write_exact(out, YaneBinBookMagic.data(), YaneBinBookMagic.size(), path);
    write_u64_le(out, record_count, path);
    write_u64_le(out, flags, path);
}

YaneBinBookIndexEntry read_ybb_index_entry(std::istream& in, const std::filesystem::path& path)
{
    YaneBinBookIndexEntry entry;
    read_exact(in, reinterpret_cast<char*>(entry.key.bytes.data()), entry.key.bytes.size(), path);
    entry.moves_offset = read_u64_le(in, path);
    entry.ply = read_u16_le(in, path);
    entry.move_count = read_u16_le(in, path);
    return entry;
}

void write_ybb_index_entry(std::ostream& out, const YaneBinBookIndexEntry& entry, const std::filesystem::path& path)
{
    write_exact(out, reinterpret_cast<const char*>(entry.key.bytes.data()), entry.key.bytes.size(), path);
    write_u64_le(out, entry.moves_offset, path);
    write_u16_le(out, entry.ply, path);
    write_u16_le(out, entry.move_count, path);
}

std::uint64_t file_size_u64(const std::filesystem::path& path)
{
    const auto size = std::filesystem::file_size(path);
    return static_cast<std::uint64_t>(size);
}

bool packed_sfen_less(const PackedSfen& lhs, const PackedSfen& rhs)
{
    return lhs.bytes < rhs.bytes;
}

bool packed_sfen_equal(const PackedSfen& lhs, const PackedSfen& rhs)
{
    return lhs.bytes == rhs.bytes;
}

std::vector<BookEntry> make_sorted_run_from_map(const BookStore::Map& map)
{
    std::vector<BookEntry> run;
    run.reserve(map.size());
    for (const auto& [key, position] : map)
        run.push_back(BookEntry{key, position});
    std::sort(run.begin(), run.end(), [](const BookEntry& lhs, const BookEntry& rhs) {
        return packed_sfen_less(lhs.key, rhs.key);
    });
    return run;
}

std::size_t run_level(std::size_t size)
{
    std::size_t level = 0;
    std::size_t capacity = LsmMemtableFlushThreshold;
    size = std::max<std::size_t>(1, size);
    while (capacity < size && capacity <= std::numeric_limits<std::size_t>::max() / 2)
    {
        capacity *= 2;
        ++level;
    }
    return level;
}

std::vector<BookEntry> merge_sorted_runs(const std::vector<BookEntry>& older, const std::vector<BookEntry>& newer)
{
    std::vector<BookEntry> merged;
    merged.reserve(older.size() + newer.size());

    std::size_t older_index = 0;
    std::size_t newer_index = 0;
    while (older_index < older.size() || newer_index < newer.size())
    {
        if (older_index == older.size())
        {
            merged.push_back(newer[newer_index++]);
            continue;
        }
        if (newer_index == newer.size())
        {
            merged.push_back(older[older_index++]);
            continue;
        }

        const auto& older_entry = older[older_index];
        const auto& newer_entry = newer[newer_index];
        if (packed_sfen_less(older_entry.key, newer_entry.key))
        {
            merged.push_back(older_entry);
            ++older_index;
        }
        else if (packed_sfen_less(newer_entry.key, older_entry.key))
        {
            merged.push_back(newer_entry);
            ++newer_index;
        }
        else
        {
            merged.push_back(newer_entry);
            ++older_index;
            ++newer_index;
        }
    }

    return merged;
}

template <typename RunT>
auto find_entry_in_run(RunT& run, const PackedSfen& key)
{
    auto it = std::lower_bound(run.begin(), run.end(), key, [](const auto& entry, const PackedSfen& target) {
        return packed_sfen_less(entry.key, target);
    });
    if (it == run.end() || !packed_sfen_equal(it->key, key))
        return run.end();
    return it;
}

void sort_and_adjust_moves(std::vector<MoveInfo>& moves)
{
    std::sort(moves.begin(), moves.end(), [](const MoveInfo& lhs, const MoveInfo& rhs) {
        return lhs.eval > rhs.eval;
    });

    if (moves.size() >= 2 && moves[0].eval == moves[1].eval)
    {
        const auto best_eval = moves[0].eval;
        for (std::size_t i = 1; i < moves.size(); ++i)
        {
            if (moves[i].eval != best_eval)
                break;
            --moves[i].eval;
        }
    }
}

} // namespace

std::size_t BookStore::size() const
{
    std::scoped_lock lock(mutex_);
    return size_;
}

bool BookStore::empty() const
{
    std::scoped_lock lock(mutex_);
    return size_ == 0;
}

void BookStore::clear()
{
    std::scoped_lock lock(mutex_);
    size_ = 0;
    memtable_.clear();
    runs_.clear();
    searching_.clear();
}

PositionInfo* BookStore::find_position_locked(const PackedSfen& key)
{
    if (auto it = memtable_.find(key); it != memtable_.end())
        return &it->second;

    for (auto run_it = runs_.rbegin(); run_it != runs_.rend(); ++run_it)
    {
        auto entry_it = find_entry_in_run(*run_it, key);
        if (entry_it != run_it->end())
            return &entry_it->position;
    }
    return nullptr;
}

const PositionInfo* BookStore::find_position_locked(const PackedSfen& key) const
{
    if (auto it = memtable_.find(key); it != memtable_.end())
        return &it->second;

    for (auto run_it = runs_.rbegin(); run_it != runs_.rend(); ++run_it)
    {
        auto entry_it = find_entry_in_run(*run_it, key);
        if (entry_it != run_it->end())
            return &entry_it->position;
    }
    return nullptr;
}

void BookStore::flush_memtable_locked()
{
    if (memtable_.empty())
        return;

    runs_.push_back(make_sorted_run_from_map(memtable_));
    memtable_.clear();
    compact_runs_locked();
}

void BookStore::compact_runs_locked()
{
    while (true)
    {
        std::optional<std::size_t> first;
        std::optional<std::size_t> second;
        for (std::size_t i = 0; i < runs_.size(); ++i)
        {
            for (std::size_t j = i + 1; j < runs_.size(); ++j)
            {
                if (run_level(runs_[i].size()) == run_level(runs_[j].size()))
                {
                    first = i;
                    second = j;
                    break;
                }
            }
            if (first.has_value())
                break;
        }

        if (!first.has_value() || !second.has_value())
            return;

        auto merged = merge_sorted_runs(runs_[*first], runs_[*second]);
        runs_.erase(runs_.begin() + static_cast<std::ptrdiff_t>(*second));
        runs_.erase(runs_.begin() + static_cast<std::ptrdiff_t>(*first));
        runs_.push_back(std::move(merged));
    }
}

std::size_t BookStore::count_save_positions_locked(std::optional<int> ply_limit) const
{
    std::size_t count = 0;
    auto count_position = [&](const PositionInfo& position) {
        if (position.moves.empty())
            return;
        if (ply_limit.has_value() && position.ply > *ply_limit)
            return;
        ++count;
    };

    for (const auto& run : runs_)
        for (const auto& entry : run)
            count_position(entry.position);
    for (const auto& [_, position] : memtable_)
        count_position(position);
    return count;
}

std::vector<BookStore::Entry> BookStore::snapshot_save_entries_locked(std::optional<int> ply_limit) const
{
    std::vector<Entry> entries;
    entries.reserve(size_);

    auto append_entry = [&](const PackedSfen& key, const PositionInfo& position) {
        if (position.moves.empty())
            return;
        if (ply_limit.has_value() && position.ply > *ply_limit)
            return;
        entries.push_back(Entry{key, position});
    };

    for (const auto& run : runs_)
        for (const auto& entry : run)
            append_entry(entry.key, entry.position);
    for (const auto& [key, position] : memtable_)
        append_entry(key, position);

    return entries;
}

PackedSfen PackedSfen::from_sfen(const std::string& sfen)
{
    PackedSfen key;
    key.bytes = pack_sfen_bytes(sfen);
    return key;
}

PackedSfen PackedSfen::flipped() const
{
    PackedSfen key;
    key.bytes = flip_packed_sfen_bytes(bytes);
    return key;
}

void PackedSfen::flip()
{
    bytes = flip_packed_sfen_bytes(bytes);
}

std::size_t PackedSfenHash::operator()(const PackedSfen& key) const noexcept
{
    static_assert(sizeof(PackedSfen) % sizeof(std::size_t) == 0);

    std::size_t hash = 0;
    for (std::size_t i = 0; i < key.bytes.size(); i += sizeof(std::size_t))
    {
        std::size_t chunk = 0;
        std::memcpy(&chunk, key.bytes.data() + i, sizeof(std::size_t));
        hash ^= chunk;
    }
    return hash;
}

std::uint16_t move16_from_usi(const std::string& usi)
{
    return yaneuraou_move16_from_usi(usi);
}

std::string move16_to_usi(std::uint16_t move16)
{
    return yaneuraou_move16_to_usi(move16);
}

std::int16_t normalize_book_eval(int eval)
{
    const int abs_eval = eval < 0 ? -eval : eval;
    const int sign = eval < 0 ? -1 : 1;

    int normalized = eval;
    if (abs_eval > OldBookMateThreshold)
    {
        const int mate_distance = OldBookValueMate - abs_eval;
        normalized = sign * (ValueMate - mate_distance);
    }
    else if (abs_eval > ValueEvalClamp)
    {
        normalized = sign * ValueMate;
    }

    if (normalized > std::numeric_limits<std::int16_t>::max())
        return std::numeric_limits<std::int16_t>::max();
    if (normalized < std::numeric_limits<std::int16_t>::min())
        return std::numeric_limits<std::int16_t>::min();
    return static_cast<std::int16_t>(normalized);
}

std::pair<std::string, int> trim_sfen_ply(const std::string& sfen_with_optional_prefix_and_ply)
{
    auto tokens = split_ws(sfen_with_optional_prefix_and_ply);
    if (!tokens.empty() && tokens.front() == "sfen")
        tokens.erase(tokens.begin());

    int ply = 0;
    if (!tokens.empty())
    {
        int parsed = 0;
        if (parse_int(tokens.back(), parsed))
        {
            ply = parsed;
            tokens.pop_back();
        }
    }

    return {join_tokens(tokens), ply};
}

std::string trim_sfen(const std::string& sfen_with_optional_prefix_and_ply)
{
    return trim_sfen_ply(sfen_with_optional_prefix_and_ply).first;
}

std::filesystem::path temp_book_path(const std::filesystem::path& path)
{
    const auto extension = path.extension().string();
    if (extension == ".db" || extension == ".ybb")
        return path.parent_path() / ("tmp-" + path.stem().string() + extension);
    return std::filesystem::path(path.string() + ".tmp");
}

bool is_yane_bin_book_path(const std::filesystem::path& path)
{
    const auto filename = path.filename().string();
    return path.extension() == ".ybb"
        && !has_suffix(filename, "-index.ybb")
        && !has_suffix(filename, "-moves.ybb");
}

void BookStore::load_yaneuraou_book(const std::filesystem::path& path, bool normalize_eval, BookProgressCallback progress, void* user)
{
    if (is_yane_bin_book_path(path))
    {
        clear();
        std::ifstream index_in(path, std::ios::binary);
        if (!index_in)
            throw std::runtime_error("failed to open ybb book: " + path.string());
        std::ifstream moves_in(path, std::ios::binary);
        if (!moves_in)
            throw std::runtime_error("failed to open ybb book: " + path.string());

        const auto header = read_ybb_header(index_in, path);
        const std::uint64_t record_count = header.record_count;
        const std::uint64_t move_record_size = (header.flags & YaneBinBookFlagMoveDepth)
            ? YaneBinBookMoveDepthRecordSize
            : YaneBinBookMoveRecordSize;
        const std::uint64_t expected_index_size = YaneBinBookHeaderSize + record_count * YaneBinBookIndexRecordSize;
        const std::uint64_t file_size = file_size_u64(path);
        if (file_size < expected_index_size)
            throw std::runtime_error("invalid ybb file size: " + path.string());
        const std::uint64_t moves_base = expected_index_size;
        const std::uint64_t moves_size = file_size - moves_base;

        report_progress(progress, user, BookProgressKind::Start, 0, static_cast<std::size_t>(record_count), path);

        Run run;
        run.reserve(static_cast<std::size_t>(record_count));
        PackedSfen previous_key{};
        bool has_previous = false;
        for (std::uint64_t i = 0; i < record_count; ++i)
        {
            const auto entry = read_ybb_index_entry(index_in, path);
            if (has_previous && !(previous_key.bytes < entry.key.bytes))
                throw std::runtime_error("ybb index is not strictly sorted: " + path.string());
            previous_key = entry.key;
            has_previous = true;

            const std::uint64_t move_bytes = static_cast<std::uint64_t>(entry.move_count) * move_record_size;
            if (entry.moves_offset > moves_size || move_bytes > moves_size - entry.moves_offset)
                throw std::runtime_error("ybb moves offset is out of range: " + path.string());

            moves_in.seekg(static_cast<std::streamoff>(moves_base + entry.moves_offset));
            if (!moves_in)
                throw std::runtime_error("failed to seek ybb book: " + path.string());

            PositionInfo position;
            position.ply = entry.ply;
            position.moves.reserve(entry.move_count);
            for (std::uint16_t j = 0; j < entry.move_count; ++j)
            {
                MoveInfo move;
                move.move16 = read_u16_le(moves_in, path);
                move.eval = static_cast<std::int16_t>(read_u16_le(moves_in, path));
                if (header.flags & YaneBinBookFlagMoveDepth)
                    (void)read_u16_le(moves_in, path);
                if (normalize_eval)
                    move.eval = normalize_book_eval(move.eval);
                position.moves.push_back(move);
            }

            run.push_back(Entry{entry.key, std::move(position)});
            const auto done = static_cast<std::size_t>(i + 1);
            if (done % BookReadProgressInterval == 0)
                report_progress(progress, user, BookProgressKind::Progress, done, static_cast<std::size_t>(record_count), path);
        }

        {
            std::scoped_lock lock(mutex_);
            size_ = run.size();
            memtable_.clear();
            runs_.clear();
            if (!run.empty())
                runs_.push_back(std::move(run));
            searching_.clear();
        }
        report_progress(progress, user, BookProgressKind::Done, static_cast<std::size_t>(record_count), static_cast<std::size_t>(record_count), path);
        return;
    }

    if (has_suffix(path.filename().string(), "-index.ybb") || has_suffix(path.filename().string(), "-moves.ybb"))
        throw std::runtime_error("split ybb is not supported. specify a single .ybb path: " + path.string());

    clear();

    std::ifstream in(path);
    if (!in)
        throw std::runtime_error("failed to open book: " + path.string());

    std::optional<std::size_t> total;
    std::string current_sfen;
    int current_ply = 0;
    std::vector<MoveInfo> current_moves;
    std::size_t sfen_line_count = 0;
    Map loaded;

    report_progress(progress, user, BookProgressKind::Start, 0, total, path);

    auto append_position = [&]() {
        if (current_sfen.empty())
            return;

        if (!current_moves.empty())
        {
            const PackedSfen key = PackedSfen::from_sfen(current_sfen);
            auto& position = loaded[key];
            if (position.moves.empty())
                position.ply = static_cast<std::uint16_t>(std::max(0, current_ply));
            position.moves.insert(position.moves.end(), current_moves.begin(), current_moves.end());
        }

        current_sfen.clear();
        current_ply = 0;
        current_moves.clear();
    };

    std::string line;
    while (std::getline(in, line))
    {
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (line.empty())
            continue;
        if (line.size() >= 3 &&
            static_cast<unsigned char>(line[0]) == 0xef &&
            static_cast<unsigned char>(line[1]) == 0xbb &&
            static_cast<unsigned char>(line[2]) == 0xbf)
        {
            line.erase(0, 3);
        }

        if (line.find(YaneuraOuBookHeaderV1) != std::string::npos)
            continue;

        if (starts_with(line, "#"))
        {
            const std::string marker = "# NOE:";
            if (starts_with(line, marker))
            {
                int parsed_total = 0;
                if (parse_int(line.substr(marker.size()), parsed_total) && parsed_total >= 0)
                {
                    total = static_cast<std::size_t>(parsed_total);
                    report_progress(progress, user, BookProgressKind::Start, 0, total, path);
                }
            }
            continue;
        }

        if (starts_with(line, "sfen "))
        {
            append_position();
            ++sfen_line_count;
            if (sfen_line_count % BookReadProgressInterval == 0)
                report_progress(progress, user, BookProgressKind::Progress, sfen_line_count, total, path);

            auto [sfen, ply] = trim_sfen_ply(line);
            current_sfen = std::move(sfen);
            current_ply = ply;
        }
        else
        {
            const auto parsed = parse_book_move_line(line, normalize_eval);
            if (parsed.eval.has_value())
                current_moves.push_back(MoveInfo{parsed.move16, *parsed.eval});
        }
    }

    append_position();
    auto run = make_sorted_run_from_map(loaded);
    {
        std::scoped_lock lock(mutex_);
        size_ = run.size();
        memtable_.clear();
        runs_.clear();
        if (!run.empty())
            runs_.push_back(std::move(run));
        searching_.clear();
    }
    report_progress(progress, user, BookProgressKind::Done, sfen_line_count, total, path);
}

PositionInfo* BookStore::find_position(const std::string& sfen)
{
    const PackedSfen key = PackedSfen::from_sfen(sfen);
    std::scoped_lock lock(mutex_);
    return find_position_locked(key);
}

const PositionInfo* BookStore::find_position(const std::string& sfen) const
{
    const PackedSfen key = PackedSfen::from_sfen(sfen);
    std::scoped_lock lock(mutex_);
    return find_position_locked(key);
}

const PositionInfo* BookStore::find_position(const PackedSfen& key) const
{
    std::scoped_lock lock(mutex_);
    return find_position_locked(key);
}

std::optional<PositionInfo> BookStore::find_position_copy(const std::string& sfen) const
{
    const PackedSfen key = PackedSfen::from_sfen(sfen);
    std::scoped_lock lock(mutex_);
    const auto* position = find_position_locked(key);
    if (position == nullptr)
        return std::nullopt;
    return *position;
}

std::optional<PositionInfo> BookStore::find_position_copy(const PackedSfen& key) const
{
    std::scoped_lock lock(mutex_);
    const auto* position = find_position_locked(key);
    if (position == nullptr)
        return std::nullopt;
    return *position;
}

SearchLease BookStore::try_begin_search(const std::string& sfen, const std::string& flipped_sfen)
{
    const PackedSfen key = PackedSfen::from_sfen(sfen);
    const PackedSfen flipped_key = PackedSfen::from_sfen(flipped_sfen);

    std::scoped_lock lock(mutex_);
    if (searching_.find(key) != searching_.end() || searching_.find(flipped_key) != searching_.end())
        return {};

    SearchLease lease;
    lease.acquired = true;
    lease.sfen = sfen;
    lease.flipped_sfen = flipped_sfen;

    if (const auto* position = find_position_locked(key))
    {
        lease.position = *position;
    }
    else if (const auto* flipped_position = find_position_locked(flipped_key))
    {
        lease.sfen = flipped_sfen;
        lease.flipped_sfen = sfen;
        lease.position = *flipped_position;
    }

    searching_.insert(key);
    searching_.insert(flipped_key);
    return lease;
}

void BookStore::end_search(const SearchLease& lease)
{
    if (!lease.acquired)
        return;

    std::scoped_lock lock(mutex_);
    searching_.erase(PackedSfen::from_sfen(lease.sfen));
    searching_.erase(PackedSfen::from_sfen(lease.flipped_sfen));
}

void BookStore::merge_position(const std::string& sfen, std::uint16_t ply, const std::vector<MoveInfo>& moves)
{
    if (moves.empty())
        return;

    const PackedSfen key = PackedSfen::from_sfen(sfen);
    std::scoped_lock lock(mutex_);
    PositionInfo* position = find_position_locked(key);
    if (position == nullptr)
    {
        position = &memtable_[key];
        ++size_;
    }
    if (position->moves.empty())
        position->ply = ply;

    for (const auto& move : moves)
    {
        auto it = std::find_if(position->moves.begin(), position->moves.end(), [&](const MoveInfo& existing) {
            return existing.move16 == move.move16;
        });
        if (it == position->moves.end())
            position->moves.push_back(move);
        else
            it->eval = move.eval;
    }

    std::sort(position->moves.begin(), position->moves.end(), [](const MoveInfo& lhs, const MoveInfo& rhs) {
        return lhs.eval > rhs.eval;
    });

    if (memtable_.size() >= LsmMemtableFlushThreshold)
        flush_memtable_locked();
}

std::size_t BookStore::count_save_positions(std::optional<int> ply_limit) const
{
    std::scoped_lock lock(mutex_);
    return count_save_positions_locked(ply_limit);
}

std::size_t BookStore::save_yaneuraou_book(
    const std::filesystem::path& path,
    std::optional<int> ply_limit,
    BookProgressCallback progress,
    void* user) const
{
    if (is_yane_bin_book_path(path))
    {
        std::filesystem::create_directories(path.parent_path());
        const auto tmp_path = temp_book_path(path);
        std::size_t position_count = 0;

        try
        {
            std::scoped_lock lock(mutex_);
            const Run memtable_run = make_sorted_run_from_map(memtable_);
            std::vector<const Run*> runs;
            runs.reserve(runs_.size() + (memtable_run.empty() ? 0 : 1));
            for (const auto& run : runs_)
                runs.push_back(&run);
            if (!memtable_run.empty())
                runs.push_back(&memtable_run);

            position_count = count_save_positions_locked(ply_limit);
            report_progress(progress, user, BookProgressKind::Start, 0, position_count, path);

            std::fstream out(tmp_path, std::ios::in | std::ios::out | std::ios::binary | std::ios::trunc);
            if (!out)
                throw std::runtime_error("failed to open temp ybb book: " + tmp_path.string());

            write_ybb_header(out, static_cast<std::uint64_t>(position_count), 0, tmp_path);
            const std::uint64_t index_size = YaneBinBookHeaderSize
                + static_cast<std::uint64_t>(position_count) * YaneBinBookIndexRecordSize;
            std::uint64_t index_offset = YaneBinBookHeaderSize;

            struct Cursor {
                const Run* run = nullptr;
                std::size_t index = 0;
            };
            std::vector<Cursor> cursors;
            cursors.reserve(runs.size());
            for (const auto* run : runs)
                if (run != nullptr && !run->empty())
                    cursors.push_back(Cursor{run, 0});

            auto current_entry = [](const Cursor& cursor) -> const Entry& {
                return (*cursor.run)[cursor.index];
            };

            auto advance_filtered = [&](Cursor& cursor) {
                while (cursor.index < cursor.run->size())
                {
                    const auto& position = current_entry(cursor).position;
                    if (!position.moves.empty() && (!ply_limit.has_value() || position.ply <= *ply_limit))
                        break;
                    ++cursor.index;
                }
            };

            auto next_entry = [&]() -> const Entry* {
                for (auto& cursor : cursors)
                    advance_filtered(cursor);

                std::optional<std::size_t> selected;
                for (std::size_t i = 0; i < cursors.size(); ++i)
                {
                    if (cursors[i].index >= cursors[i].run->size())
                        continue;
                    if (!selected.has_value() || packed_sfen_less(current_entry(cursors[i]).key, current_entry(cursors[*selected]).key))
                        selected = i;
                }
                if (!selected.has_value())
                    return nullptr;

                const PackedSfen selected_key = current_entry(cursors[*selected]).key;
                std::size_t newest_same_key = *selected;
                for (std::size_t i = 0; i < cursors.size(); ++i)
                {
                    if (cursors[i].index >= cursors[i].run->size())
                        continue;
                    if (packed_sfen_equal(current_entry(cursors[i]).key, selected_key))
                        newest_same_key = i;
                }

                const Entry* result = &current_entry(cursors[newest_same_key]);
                for (auto& cursor : cursors)
                {
                    if (cursor.index < cursor.run->size() && packed_sfen_equal(current_entry(cursor).key, selected_key))
                        ++cursor.index;
                }
                return result;
            };

            std::uint64_t moves_offset = 0;
            std::size_t count = 0;
            while (const Entry* entry_source = next_entry())
            {
                auto moves = entry_source->position.moves;
                if (moves.size() > std::numeric_limits<std::uint16_t>::max())
                    throw std::runtime_error("too many moves in a ybb position");

                sort_and_adjust_moves(moves);

                YaneBinBookIndexEntry entry;
                entry.key = entry_source->key;
                entry.moves_offset = moves_offset;
                entry.ply = entry_source->position.ply;
                entry.move_count = static_cast<std::uint16_t>(moves.size());

                out.seekp(static_cast<std::streamoff>(index_size + moves_offset));
                if (!out)
                    throw std::runtime_error("failed to seek temp ybb moves area: " + tmp_path.string());
                for (const auto& move : moves)
                {
                    write_u16_le(out, move.move16, tmp_path);
                    write_i16_le(out, move.eval, tmp_path);
                    moves_offset += YaneBinBookMoveRecordSize;
                }

                out.seekp(static_cast<std::streamoff>(index_offset));
                if (!out)
                    throw std::runtime_error("failed to seek temp ybb index area: " + tmp_path.string());
                write_ybb_index_entry(out, entry, tmp_path);
                index_offset += YaneBinBookIndexRecordSize;

                ++count;
                if (count % BookWriteProgressInterval == 0)
                    report_progress(progress, user, BookProgressKind::Progress, count, position_count, path);
            }

            out.close();
            if (!out)
                throw std::runtime_error("failed to write temp ybb book: " + tmp_path.string());

            std::error_code ec;
            std::filesystem::remove(path, ec);
            std::filesystem::rename(tmp_path, path);
            report_progress(progress, user, BookProgressKind::Done, position_count, position_count, path);
        }
        catch (...)
        {
            std::error_code ec;
            std::filesystem::remove(tmp_path, ec);
            throw;
        }

        return position_count;
    }

    if (has_suffix(path.filename().string(), "-index.ybb") || has_suffix(path.filename().string(), "-moves.ybb"))
        throw std::runtime_error("saving split ybb is no longer supported. specify a single .ybb path: " + path.string());

    struct SaveEntry {
        PackedSfen key;
        std::uint16_t ply = 0;
        std::string sfen;
        std::vector<MoveInfo> moves;
    };

    std::vector<SaveEntry> positions;
    {
        std::scoped_lock lock(mutex_);
        const auto entries = snapshot_save_entries_locked(ply_limit);
        positions.reserve(entries.size());
        for (const auto& entry : entries)
            positions.push_back(SaveEntry{entry.key, entry.position.ply, {}, entry.position.moves});
    }

    for (auto& position : positions)
        position.sfen = trim_sfen(unpack_sfen_bytes(position.key.bytes));

    std::sort(positions.begin(), positions.end(), [](const SaveEntry& lhs, const SaveEntry& rhs) {
        return lhs.sfen < rhs.sfen;
    });

    std::filesystem::create_directories(path.parent_path());
    const auto tmp_path = temp_book_path(path);
    report_progress(progress, user, BookProgressKind::Start, 0, positions.size(), path);

    try
    {
        std::ofstream out(tmp_path);
        if (!out)
            throw std::runtime_error("failed to open temp book: " + tmp_path.string());

        out << YaneuraOuBookHeaderV1 << '\n';
        out << "# NOE:" << positions.size() << '\n';

        std::size_t count = 0;
        for (const auto& position : positions)
        {
            ++count;
            out << "sfen " << position.sfen << ' ' << position.ply << '\n';

            auto moves = position.moves;
            sort_and_adjust_moves(moves);

            for (const auto& move : moves)
                out << move16_to_usi(move.move16) << " none " << move.eval << " 0\n";

            if (count % BookWriteProgressInterval == 0)
                report_progress(progress, user, BookProgressKind::Progress, count, positions.size(), path);
        }

        out.close();
        if (!out)
            throw std::runtime_error("failed to write temp book: " + tmp_path.string());

        std::filesystem::rename(tmp_path, path);
        report_progress(progress, user, BookProgressKind::Done, positions.size(), positions.size(), path);
    }
    catch (...)
    {
        std::error_code ec;
        std::filesystem::remove(tmp_path, ec);
        throw;
    }

    return positions.size();
}

} // namespace bookminer
