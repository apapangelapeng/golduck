-- Exhaustively enumerate Max151 observation classes inside one Golly process.
-- Configuration is supplied through GOLDUCK_MAX151_* environment variables.

local g = golly()

local generation = assert(tonumber(os.getenv("GOLDUCK_MAX151_GENERATION")))
local start_rank = tonumber(os.getenv("GOLDUCK_MAX151_START_RANK") or "0")
local end_rank = tonumber(os.getenv("GOLDUCK_MAX151_END_RANK") or "47321")
local output_path = assert(os.getenv("GOLDUCK_MAX151_OUTPUT"))
local window_names = os.getenv("GOLDUCK_MAX151_WINDOWS") or "legacy"

local windows = {
    legacy = {-60, 300, 120, 48},
    wide = {-180, 300, 360, 120},
    full = {-500, 300, 1000, 200},
}

local selected_windows = {}
for name in string.gmatch(window_names, "[^,]+") do
    if windows[name] == nil then error("unknown window: " .. name) end
    table.insert(selected_windows, name)
end

-- The normalized 151-cell predecessor, translated into Level 2 world space.
local base_cells = {
    2, -100, 3, -100, 2, -99, 4, -99, 5, -99, -3, -98, -2, -98, -1, -98, 3, -98, 4, -98,
    6, -98, 8, -98, 9, -98, -4, -97, -1, -97, 0, -97, 1, -97, 4, -97, 6, -97, 8, -97,
    -11, -96, -10, -96, -5, -96, -1, -96, 0, -96, 6, -96, 7, -96, 8, -96, -11, -95, -5, -95,
    1, -95, 3, -95, 5, -95, 8, -95, -14, -94, -13, -94, -12, -94, -11, -94, -3, -94, 3, -94,
    -14, -93, -6, -93, -4, -93, 6, -93, 9, -93, 11, -93, -12, -92, -9, -92, -8, -92, -6, -92,
    -5, -92, -4, -92, -2, -92, -1, -92, 6, -92, 8, -92, 10, -92, -15, -91, -10, -91, -6, -91,
    -4, -91, -2, -91, -14, -90, -12, -90, -8, -90, -6, -90, -3, -90, 0, -90, 1, -90, 9, -90,
    -13, -89, -11, -89, -10, -89, -7, -89, -4, -89, -2, -89, 0, -89, 3, -89, 6, -89, 7, -89,
    9, -89, -13, -88, -5, -88, -4, -88, -1, -88, 2, -88, 4, -88, 8, -88, 10, -88, -2, -87,
    0, -87, 2, -87, 6, -87, 11, -87, -14, -86, -12, -86, -10, -86, -3, -86, -2, -86, 0, -86,
    1, -86, 2, -86, 4, -86, 5, -86, 8, -86, -15, -85, -13, -85, -10, -85, 0, -85, 2, -85,
    10, -85, -7, -84, -1, -84, 7, -84, 8, -84, 9, -84, 10, -84, -12, -83, -9, -83, -7, -83,
    -5, -83, 1, -83, 7, -83, -12, -82, -11, -82, -10, -82, -4, -82, -3, -82, 1, -82, 6, -82,
    7, -82, -12, -81, -10, -81, -8, -81, -5, -81, -4, -81, -3, -81, 0, -81, -13, -80, -12, -80,
    -10, -80, -8, -80, -7, -80, -3, -80, -2, -80, -1, -80, -9, -79, -8, -79, -6, -79, -7, -78,
    -6, -78,
}

local function add_secret_cell(cells, x, y)
    table.insert(cells, x)
    table.insert(cells, y)
end

local function initial_cells(context)
    local cells = {}
    for index = 1, #base_cells do cells[index] = base_cells[index] end
    for offset = 0, 11 do
        local symbol = context[offset + 1]
        if symbol ~= 0 then
            local bit = 26 + offset
            local x = -96 + 3 * bit
            local parity = symbol - 1
            add_secret_cell(cells, x, -401)
            add_secret_cell(cells, x, -400)
            add_secret_cell(cells, x + 3, -401)
            add_secret_cell(cells, x + 3, -400)
            add_secret_cell(cells, x + 1, -401 + parity)
            add_secret_cell(cells, x + 2, -400 - parity)
        end
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

local class_by_signature = {}
local class_counts = {}
for _, name in ipairs(selected_windows) do
    class_by_signature[name] = {}
    class_counts[name] = 0
end

local output = assert(io.open(output_path, "w"))
output:write("# rank")
for _, name in ipairs(selected_windows) do output:write("\t", name) end
output:write("\n")

g.new("Max151 exhaustive calibration")
g.setalgo("QuickLife")
g.setrule("B3/S23")
g.autoupdate(false)

local rank = 0
local context = {}
local completed = 0

local function evaluate()
    if rank >= start_rank and rank < end_rank then
        g.new("")
        g.putcells(initial_cells(context))
        g.run(generation)
        output:write(tostring(rank))
        for _, name in ipairs(selected_windows) do
            local rect = windows[name]
            local signature = canonical_signature(g.getcells(rect), rect)
            local class_id = class_by_signature[name][signature]
            if class_id == nil then
                class_counts[name] = class_counts[name] + 1
                class_id = class_counts[name]
                class_by_signature[name][signature] = class_id
            end
            output:write("\t", signature)
        end
        output:write("\n")
        completed = completed + 1
        if completed % 1000 == 0 then
            output:flush()
            print(string.format("generation %d: %d contexts", generation, completed))
        end
    end
    rank = rank + 1
end

local function visit(position, previous)
    if rank >= end_rank then return end
    if position == 12 then
        evaluate()
        return
    end
    for symbol = 0, 2 do
        if not (previous ~= 0 and symbol ~= 0 and previous ~= symbol) then
            context[position + 1] = symbol
            visit(position + 1, symbol)
        end
    end
end

visit(0, 0)
output:write("# classes")
for _, name in ipairs(selected_windows) do
    output:write("\t", name, "=", tostring(class_counts[name]))
end
output:write("\n")
output:close()
os.exit(0)
