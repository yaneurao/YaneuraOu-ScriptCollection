#include "config.h"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <limits>
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

    char string_delimiter = '\0';
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

        if (string_delimiter)
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
            if (ch == string_delimiter)
                string_delimiter = '\0';
            continue;
        }

        if (ch == '"' || ch == '\'')
        {
            string_delimiter = ch;
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
    char string_delimiter = '\0';
    bool escaped = false;

    for (std::size_t i = 0; i < text.size(); ++i)
    {
        const char ch = text[i];

        if (string_delimiter)
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
            if (ch == string_delimiter)
                string_delimiter = '\0';
            continue;
        }

        if (ch == '"' || ch == '\'')
        {
            string_delimiter = ch;
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
    return "(^|[\\s,{])[\"']?" + name + "[\"']?\\s*:\\s*";
}

unsigned parse_hex_escape(const std::string& text, std::size_t& pos, int digits, const std::string& name)
{
    unsigned value = 0;
    for (int i = 0; i < digits; ++i)
    {
        if (pos >= text.size() || !std::isxdigit(static_cast<unsigned char>(text[pos])))
            throw std::runtime_error("invalid character escape in " + name);
        const unsigned char ch = static_cast<unsigned char>(text[pos++]);
        value = value * 16 + (std::isdigit(ch) ? ch - '0' : std::tolower(ch) - 'a' + 10);
    }
    return value;
}

void append_utf8(std::string& out, unsigned codepoint)
{
    if (codepoint <= 0x7f)
        out += static_cast<char>(codepoint);
    else if (codepoint <= 0x7ff)
    {
        out += static_cast<char>(0xc0 | (codepoint >> 6));
        out += static_cast<char>(0x80 | (codepoint & 0x3f));
    }
    else if (codepoint <= 0xffff)
    {
        out += static_cast<char>(0xe0 | (codepoint >> 12));
        out += static_cast<char>(0x80 | ((codepoint >> 6) & 0x3f));
        out += static_cast<char>(0x80 | (codepoint & 0x3f));
    }
    else
    {
        out += static_cast<char>(0xf0 | (codepoint >> 18));
        out += static_cast<char>(0x80 | ((codepoint >> 12) & 0x3f));
        out += static_cast<char>(0x80 | ((codepoint >> 6) & 0x3f));
        out += static_cast<char>(0x80 | (codepoint & 0x3f));
    }
}

std::optional<std::string> string_field(const std::string& object, const std::string& name)
{
    const std::regex pattern(field_prefix(name));
    std::smatch match;
    if (!std::regex_search(object, match, pattern))
        return std::nullopt;
    std::size_t pos = static_cast<std::size_t>(match.position() + match.length());
    if (pos >= object.size() || (object[pos] != '"' && object[pos] != '\''))
        throw std::runtime_error("expected string for " + name);
    const char quote = object[pos++];
    std::string value;
    while (pos < object.size())
    {
        char ch = object[pos++];
        if (ch == quote)
            return value;
        if (ch != '\\')
        {
            value += ch;
            continue;
        }
        if (pos >= object.size())
            break;
        ch = object[pos++];
        switch (ch)
        {
        case 'n': value += '\n'; break;
        case 'r': value += '\r'; break;
        case 't': value += '\t'; break;
        case 'b': value += '\b'; break;
        case 'f': value += '\f'; break;
        case '\n': break;
        case '\r': if (pos < object.size() && object[pos] == '\n') ++pos; break;
        case 'x': append_utf8(value, parse_hex_escape(object, pos, 2, name)); break;
        case 'u': {
            unsigned codepoint = parse_hex_escape(object, pos, 4, name);
            if (codepoint >= 0xd800 && codepoint <= 0xdbff)
            {
                if (pos + 2 > object.size() || object[pos] != '\\' || object[pos + 1] != 'u')
                    throw std::runtime_error("invalid unicode surrogate in " + name);
                pos += 2;
                const unsigned low = parse_hex_escape(object, pos, 4, name);
                if (low < 0xdc00 || low > 0xdfff)
                    throw std::runtime_error("invalid unicode surrogate in " + name);
                codepoint = 0x10000 + ((codepoint - 0xd800) << 10) + (low - 0xdc00);
            }
            else if (codepoint >= 0xdc00 && codepoint <= 0xdfff)
                throw std::runtime_error("invalid unicode surrogate in " + name);
            append_utf8(value, codepoint);
            break;
        }
        default: value += ch; break;
        }
    }
    throw std::runtime_error("unterminated string for " + name);
}

std::optional<long long> number_field(const std::string& object, const std::string& name)
{
    const std::regex pattern(field_prefix(name) + "(-?[0-9]+)");
    std::smatch match;
    if (!std::regex_search(object, match, pattern))
        return std::nullopt;
    return std::stoll(match[2].str());
}

std::optional<std::uint32_t> positive_u32_field(const std::string& object, const std::string& name)
{
    const std::regex pattern(field_prefix(name));
    std::smatch match;
    if (!std::regex_search(object, match, pattern))
        return std::nullopt;
    const auto pos = static_cast<std::size_t>(match.position() + match.length());
    auto end = pos;
    while (end < object.size() && !std::isspace(static_cast<unsigned char>(object[end]))
           && object[end] != ',' && object[end] != '}' && object[end] != ']')
        ++end;
    const auto text = object.substr(pos, end - pos);
    try
    {
        std::size_t parsed = 0;
        const auto value = std::stoll(text, &parsed, 10);
        if (parsed == text.size() && value > 0
            && static_cast<unsigned long long>(value) <= std::numeric_limits<std::uint32_t>::max())
            return static_cast<std::uint32_t>(value);
    }
    catch (const std::exception&) {}
    throw std::runtime_error("invalid positive integer for " + name + ": " + text);
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
    const auto trim_setting = [](const std::string& value, const std::string& name) {
        const auto first = value.find_first_not_of(" \t\r\n");
        if (first == std::string::npos)
            throw std::runtime_error(name + " must not be empty");
        return value.substr(first, value.find_last_not_of(" \t\r\n") - first + 1);
    };
    if (auto value = string_field(text, "peta_next_start_sfens_path"))
        settings.peta_next_start_sfens_path = trim_setting(*value, "peta_next_start_sfens_path");
    if (auto value = string_field(text, "peta_shock_engine_path"))
        settings.peta_shock_engine_path = trim_setting(*value, "peta_shock_engine_path");
    if (auto value = positive_u32_field(text, "peta_shock_threads"))
        settings.peta_shock_threads = *value;

    return settings;
}

} // namespace bookminer
