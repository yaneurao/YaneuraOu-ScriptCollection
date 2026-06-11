#include "config.h"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <optional>
#include <regex>
#include <sstream>
#include <stdexcept>

namespace bookminer {

namespace {

std::string read_text_file(const std::filesystem::path& path)
{
    std::ifstream in(path);
    if (!in)
        throw std::runtime_error("failed to open settings file: " + path.string());
    std::ostringstream oss;
    oss << in.rdbuf();
    return oss.str();
}

std::string strip_json5_comments(const std::string& text)
{
    std::string out;
    out.reserve(text.size());

    bool in_string = false;
    bool escaped = false;
    bool line_comment = false;
    bool block_comment = false;

    for (std::size_t i = 0; i < text.size(); ++i)
    {
        const char ch = text[i];
        const char next = i + 1 < text.size() ? text[i + 1] : '\0';

        if (line_comment)
        {
            if (ch == '\n')
            {
                line_comment = false;
                out += ch;
            }
            continue;
        }

        if (block_comment)
        {
            if (ch == '*' && next == '/')
            {
                block_comment = false;
                ++i;
            }
            continue;
        }

        if (in_string)
        {
            out += ch;
            if (escaped)
            {
                escaped = false;
                continue;
            }
            if (ch == '\\')
            {
                escaped = true;
                continue;
            }
            if (ch == '"')
                in_string = false;
            continue;
        }

        if (ch == '"')
        {
            in_string = true;
            out += ch;
            continue;
        }

        if (ch == '/' && next == '/')
        {
            line_comment = true;
            ++i;
            continue;
        }

        if (ch == '/' && next == '*')
        {
            block_comment = true;
            ++i;
            continue;
        }

        out += ch;
    }

    return out;
}

std::vector<std::string> leaf_object_blocks(const std::string& text)
{
    struct Frame {
        std::size_t start = 0;
        int children = 0;
    };

    std::vector<Frame> stack;
    std::vector<std::string> blocks;
    bool in_string = false;
    bool escaped = false;

    for (std::size_t i = 0; i < text.size(); ++i)
    {
        const char ch = text[i];

        if (in_string)
        {
            if (escaped)
            {
                escaped = false;
                continue;
            }
            if (ch == '\\')
            {
                escaped = true;
                continue;
            }
            if (ch == '"')
                in_string = false;
            continue;
        }

        if (ch == '"')
        {
            in_string = true;
            continue;
        }

        if (ch == '{')
        {
            if (!stack.empty())
                ++stack.back().children;
            stack.push_back(Frame{i, 0});
            continue;
        }

        if (ch == '}' && !stack.empty())
        {
            const Frame frame = stack.back();
            stack.pop_back();
            if (frame.children == 0 && i >= frame.start)
                blocks.push_back(text.substr(frame.start, i - frame.start + 1));
        }
    }

    return blocks;
}

std::string field_prefix(const std::string& name)
{
    return "(^|[\\s,{])" + name + "\\s*:\\s*";
}

std::optional<std::string> string_field(const std::string& object, const std::string& name)
{
    const std::regex pattern(field_prefix(name) + "\"([^\"]*)\"");
    std::smatch match;
    if (!std::regex_search(object, match, pattern))
        return std::nullopt;
    return match[2].str();
}

std::optional<long long> number_field(const std::string& object, const std::string& name)
{
    const std::regex pattern(field_prefix(name) + "(-?[0-9]+)");
    std::smatch match;
    if (!std::regex_search(object, match, pattern))
        return std::nullopt;
    return std::stoll(match[2].str());
}

std::string filename_stem_or_default(const std::string& path, const std::string& fallback)
{
    if (path.empty())
        return fallback;
    const auto stem = std::filesystem::path(path).stem().string();
    return stem.empty() ? fallback : stem;
}

} // namespace

std::vector<EngineConfig> load_engine_settings(const std::filesystem::path& path)
{
    if (!std::filesystem::is_regular_file(path))
        return {};

    const std::string text = strip_json5_comments(read_text_file(path));
    std::vector<EngineConfig> engines;

    for (const auto& object : leaf_object_blocks(text))
    {
        auto path_value = string_field(object, "path");
        if (!path_value.has_value())
            continue;

        EngineConfig config;
        config.path = *path_value;
        config.name = string_field(object, "name").value_or(filename_stem_or_default(config.path, "engine"));

        if (auto value = number_field(object, "nodes"))
            config.nodes = static_cast<std::uint64_t>(std::max<long long>(1, *value));
        if (auto value = number_field(object, "multi"))
            config.multi = static_cast<int>(std::max<long long>(1, *value));
        if (auto value = number_field(object, "multipv"))
            config.multipv = static_cast<int>(std::max<long long>(1, *value));
        if (auto value = number_field(object, "multipv_delta"))
            config.multipv_delta = static_cast<int>(std::max<long long>(0, *value));

        engines.push_back(std::move(config));
    }

    return engines;
}

BookMinerSettings load_book_miner_settings(const std::vector<std::filesystem::path>& candidate_paths)
{
    BookMinerSettings settings;

    std::filesystem::path selected;
    for (const auto& path : candidate_paths)
    {
        if (std::filesystem::is_regular_file(path))
        {
            selected = path;
            break;
        }
    }

    if (selected.empty())
        return settings;

    const std::string text = strip_json5_comments(read_text_file(selected));

    if (auto value = number_field(text, "auto_save_interval_seconds"))
        settings.auto_save_interval_seconds = static_cast<int>(std::max<long long>(1, *value));
    if (auto value = number_field(text, "max_book_ply"))
        settings.max_book_ply = static_cast<int>(std::max<long long>(1, *value));
    if (auto value = string_field(text, "peta_next_start_sfens_path"))
        settings.peta_next_start_sfens_path = *value;

    return settings;
}

} // namespace bookminer
