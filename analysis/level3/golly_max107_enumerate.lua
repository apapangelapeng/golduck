-- Exhaustively enumerate Level 3 Max107 return classes inside one Golly process.
-- Configuration is supplied through GOLDUCK_L3_MAX107_* environment variables.

local g = golly()

local generation = assert(tonumber(os.getenv("GOLDUCK_L3_MAX107_GENERATION")))
local start_rank = tonumber(os.getenv("GOLDUCK_L3_MAX107_START_RANK") or "0")
local end_rank = tonumber(os.getenv("GOLDUCK_L3_MAX107_END_RANK") or "65536")
local output_path = assert(os.getenv("GOLDUCK_L3_MAX107_OUTPUT"))
local algorithm = os.getenv("GOLDUCK_L3_MAX107_ALGORITHM") or "HashLife"
local window_names = os.getenv("GOLDUCK_L3_MAX107_WINDOWS") or "narrow"

local windows = {
    narrow = {-130, -100, 120, 48},
    tall = {-130, -100, 120, 200},
    wide = {-250, -100, 500, 200},
    full = {-500, -100, 1000, 200},
}

local selected_windows = {}
for name in string.gmatch(window_names, "[^,]+") do
    if windows[name] == nil then error("unknown window: " .. name) end
    table.insert(selected_windows, name)
end

local function add_cell(cells, x, y)
    table.insert(cells, x)
    table.insert(cells, y)
end

-- The normalized 107-cell predecessor used by max107_adaptive11.  Its
-- Level 3 launch begins three cells left of the first hexadecimal glyph.
local max107_body = table.concat({
    "19b3o$13bo5bo2bo$13bo5bo$11bo2bo4bo$11b4o4bo$3bo7b2o3bo2bo$",
    "o9bo9bo$b2o5bo3bo10bo$4bo3bo3bo9bo$2obob2obo3bo3b2o4bo$",
    "4bo5bo3b2o4bo3bo$2bob2o2bo3bo3bo2b2obo$o3bo4b2o3bo5bo$",
    "2bo4b2o3bo3bob2obob2o$2bo9bo3bo3bo$bo10bo3bo5b2o$",
    "4bo9bo9bo$5bo2bo3b2o7bo$5bo4b4o$5bo4bo2bo$5bo5bo$",
    "2bo2bo5bo$3b3o!",
})

local function put_rle(cells, body, origin_x, origin_y)
    local x, y, count = 0, 0, 0
    for index = 1, #body do
        local character = string.sub(body, index, index)
        local digit = tonumber(character)
        if digit ~= nil then
            count = count * 10 + digit
        elseif character == "b" or character == "o" then
            local repeat_count = count == 0 and 1 or count
            count = 0
            if character == "o" then
                for offset = 0, repeat_count - 1 do
                    add_cell(cells, origin_x + x + offset, origin_y + y)
                end
            end
            x = x + repeat_count
        elseif character == "$" then
            local repeat_count = count == 0 and 1 or count
            count = 0
            y = y + repeat_count
            x = 0
        elseif character == "!" then
            return
        end
    end
end

-- Seven-segment glyphs from golduck/level3.py.  Each segment is a six-cell
-- asymmetric still-life predecessor; the asymmetry is part of the encoding.
local segments = {
    a = {{2,0}, {3,0}, {5,0}, {2,1}, {4,1}, {5,1}},
    b = {{6,2}, {7,2}, {6,3}, {7,4}, {6,5}, {7,5}},
    c = {{6,8}, {7,8}, {6,9}, {7,10}, {6,11}, {7,11}},
    d = {{2,12}, {3,12}, {5,12}, {2,13}, {4,13}, {5,13}},
    e = {{0,8}, {1,8}, {0,9}, {1,10}, {0,11}, {1,11}},
    f = {{0,2}, {1,2}, {0,3}, {1,4}, {0,5}, {1,5}},
    g = {{2,6}, {3,6}, {5,6}, {2,7}, {4,7}, {5,7}},
}

local digit_segments = {
    [0]  = "abcdef", [1]  = "bc",      [2]  = "abdeg",
    [3]  = "abcdg",  [4]  = "bcfg",    [5]  = "acdfg",
    [6]  = "acdefg", [7]  = "abc",     [8]  = "abcdefg",
    [9]  = "abcdfg", [10] = "abcefg",  [11] = "cdefg",
    [12] = "adef",   [13] = "bcdeg",   [14] = "adefg",
    [15] = "aefg",
}

local function put_digit(cells, digit, origin_x, origin_y)
    local names = digit_segments[digit]
    for index = 1, #names do
        local segment = segments[string.sub(names, index, index)]
        for _, cell in ipairs(segment) do
            add_cell(cells, origin_x + cell[1], origin_y + cell[2])
        end
    end
end

local function initial_cells(rank)
    local cells = {}
    put_rle(cells, max107_body, -82, 350)
    for position = 0, 15 do
        local digit = 0
        if position < 4 then
            local shift = 4 * (3 - position)
            digit = (rank >> shift) & 15
        end
        put_digit(cells, digit, -79 + 10 * position, -400)
    end
    return cells
end

local function canonical_signature(cells, rect)
    local keys = {}
    for index = 1, #cells, 2 do
        local x = cells[index] - rect[1]
        local y = cells[index + 1] - rect[2]
        table.insert(keys, y * rect[3] + x)
    end
    table.sort(keys)
    local hash1 = 1469598103934665603
    local hash2 = 7809847782465536322
    for _, key in ipairs(keys) do
        hash1 = (hash1 ~ key) * 1099511628211
        hash2 = (hash2 ~ (key + 1442695040888963407)) * 6364136223846793005
    end
    return string.format("%016x%016x", hash1, hash2)
end

local output = assert(io.open(output_path, "w"))
output:write("# rank")
for _, name in ipairs(selected_windows) do output:write("\t", name) end
output:write("\n")

g.new("Level 3 Max107 exhaustive calibration")
g.setalgo(algorithm)
g.setrule("B3/S23")
g.autoupdate(false)

for rank = start_rank, end_rank - 1 do
    g.new("")
    g.putcells(initial_cells(rank))
    g.run(generation)
    output:write(tostring(rank))
    for _, name in ipairs(selected_windows) do
        local rect = windows[name]
        output:write("\t", canonical_signature(g.getcells(rect), rect))
    end
    output:write("\n")
    if (rank - start_rank + 1) % 1000 == 0 then
        output:flush()
        print(string.format(
            "generation %d: %d contexts", generation, rank - start_rank + 1
        ))
    end
end

output:write("# complete\n")
output:close()
os.exit(0)
