typedef unsigned long long u64;

__attribute__((import_module("env"), import_name("run")))
int host_run(int level, const char *rle, int length, int generations, char *out);

__attribute__((import_module("env"), import_name("submit")))
int host_submit(int level, u64 value, u64 known_mask, u64 guess_mask);

__attribute__((import_module("env"), import_name("finalize")))
int host_finalize(void);

/* A data symbol is exported by wasm-ld as its linear-memory address. */
#define SCRATCH_CAPACITY (1024 * 1024)
char scratch_ptr[SCRATCH_CAPACITY];
char scratch_cap;

/*
 * Each input is one or two canonical northbound Copperheads.  The placements
 * are fixed relative to the level geometry; no seed-derived data is used.
 */
static const char L3_A[] =
    "x = 2000, y = 100, rule = B3/S23\n"
    "913b2o2b2o162b2o2b2o$915b2o166b2o$915b2o166b2o$"
    "912bobo2bobo160bobo2bobo$912bo6bo160bo6bo2$"
    "912bo6bo160bo6bo$913b2o2b2o162b2o2b2o$"
    "914b4o164b4o2$915b2o166b2o$915b2o166b2o!";

static const char L3_B[] =
    "x = 2000, y = 100, rule = B3/S23\n"
    "917b2o2b2o152b2o2b2o$919b2o156b2o$919b2o156b2o$"
    "916bobo2bobo150bobo2bobo$916bo6bo150bo6bo2$"
    "916bo6bo150bo6bo$917b2o2b2o152b2o2b2o$"
    "918b4o154b4o2$919b2o156b2o$919b2o156b2o!";

static const char L3_C[] =
    "x = 2000, y = 100, rule = B3/S23\n"
    "921b2o2b2o$923b2o$923b2o$920bobo2bobo$920bo6bo2$"
    "920bo6bo$921b2o2b2o$922b4o2$923b2o$923b2o!";

static const char L3_E[] =
    "x = 2000, y = 100, rule = B3/S23\n"
    "922b2o2b2o144b2o2b2o$924b2o148b2o$924b2o148b2o$"
    "921bobo2bobo142bobo2bobo$921bo6bo142bo6bo2$"
    "921bo6bo142bo6bo$922b2o2b2o144b2o2b2o$"
    "923b4o146b4o2$924b2o148b2o$924b2o148b2o!";

static const char L4_A[] =
    "x = 10000, y = 300, rule = B3/S23\n"
    "4913b2o2b2o162b2o2b2o$4915b2o166b2o$4915b2o166b2o$"
    "4912bobo2bobo160bobo2bobo$4912bo6bo160bo6bo2$"
    "4912bo6bo160bo6bo$4913b2o2b2o162b2o2b2o$"
    "4914b4o164b4o2$4915b2o166b2o$4915b2o166b2o!";

static const char L4_B[] =
    "x = 10000, y = 300, rule = B3/S23\n"
    "4917b2o2b2o152b2o2b2o$4919b2o156b2o$4919b2o156b2o$"
    "4916bobo2bobo150bobo2bobo$4916bo6bo150bo6bo2$"
    "4916bo6bo150bo6bo$4917b2o2b2o152b2o2b2o$"
    "4918b4o154b4o2$4919b2o156b2o$4919b2o156b2o!";

static const char L4_C[] =
    "x = 10000, y = 300, rule = B3/S23\n"
    "4921b2o2b2o$4923b2o$4923b2o$4920bobo2bobo$4920bo6bo2$"
    "4920bo6bo$4921b2o2b2o$4922b4o2$4923b2o$4923b2o!";

static const char L4_E[] =
    "x = 10000, y = 300, rule = B3/S23\n"
    "4922b2o2b2o144b2o2b2o$4924b2o148b2o$4924b2o148b2o$"
    "4921bobo2bobo142bobo2bobo$4921bo6bo142bo6bo2$"
    "4921bo6bo142bo6bo$4922b2o2b2o144b2o2b2o$"
    "4923b4o146b4o2$4924b2o148b2o$4924b2o148b2o!";

static int string_length(const char *text) {
  int length = 0;
  while (text[length]) ++length;
  return length;
}

/* Return the live-cell bitmap in [box_x,box_x+3) x [box_y,box_y+3). */
static unsigned region_mask(const char *rle, int length, int box_x, int box_y) {
  int index = 0;
  int x = 0;
  int y = 0;
  unsigned repeat = 0;
  unsigned mask = 0;

  /* Production output has one RLE header line. */
  while (index < length && rle[index] != '\n') ++index;
  if (index == length) return 0;
  ++index;

  while (index < length) {
    unsigned char ch = (unsigned char)rle[index++];
    if (ch >= '0' && ch <= '9') {
      repeat = repeat * 10u + (unsigned)(ch - '0');
      continue;
    }

    if (ch == 'b' || ch == 'o') {
      unsigned run = repeat ? repeat : 1u;
      repeat = 0;
      if (ch == 'o' && y >= box_y && y < box_y + 3) {
        int first = x > box_x ? x : box_x;
        int last = x + (int)run < box_x + 3 ? x + (int)run : box_x + 3;
        for (int live_x = first; live_x < last; ++live_x) {
          unsigned bit = (unsigned)((y - box_y) * 3 + live_x - box_x);
          mask |= 1u << bit;
        }
      }
      x += (int)run;
      continue;
    }

    if (ch == '$') {
      unsigned run = repeat ? repeat : 1u;
      repeat = 0;
      y += (int)run;
      x = 0;
      continue;
    }

    if (ch == '!') break;
    /* Ignore line wrapping and other whitespace. */
  }
  return mask;
}

static int observe(int level, const char *pattern, int generations) {
  return host_run(level, pattern, string_length(pattern), generations, scratch_ptr);
}

static void solve_level3(void) {
  u64 known = 0;
  u64 value = 0;
  int length;

  length = observe(3, L3_A, 8900);
  if (length > 0 && region_mask(scratch_ptr, length, 62, 67) == 0x0e9u) {
    known |= 0xf000000000000000ULL;
    value |= 0x5000000000000000ULL;
  }
  if (length > 0 && region_mask(scratch_ptr, length, 930, 9) == 0x0f1u) {
    known |= 0x000000000000000fULL;
    value |= 0x0000000000000001ULL;
  }

  length = observe(3, L3_B, 8900);
  if (!(known & 0xf000000000000000ULL) && length > 0 &&
      region_mask(scratch_ptr, length, 61, 33) == 0x0e9u) {
    known |= 0xf000000000000000ULL;
    value |= 0xc000000000000000ULL;
  }
  if (!(known & 0x000000000000000fULL) && length > 0 &&
      region_mask(scratch_ptr, length, 962, 84) == 0x0f1u) {
    known |= 0x000000000000000fULL;
    value |= 0x0000000000000009ULL;
  }

  length = observe(3, L3_C, 8900);
  if (!(known & 0xf000000000000000ULL) && length > 0 &&
      region_mask(scratch_ptr, length, 731, 71) == 0x1e2u) {
    known |= 0xf000000000000000ULL;
    value |= 0xf000000000000000ULL;
  }

  length = observe(3, L3_E, 8900);
  if (!(known & 0xf000000000000000ULL) && length > 0 &&
      region_mask(scratch_ptr, length, 813, 104) == 0x1e2u) {
    known |= 0x1000000000000000ULL;
    value |= 0x1000000000000000ULL;
  }
  if (!(known & 0x000000000000000fULL) && length > 0 &&
      region_mask(scratch_ptr, length, 963, 104) == 0x1e2u) {
    known |= 0x0000000000000001ULL;
    value |= 0x0000000000000001ULL;
  }

  if (known) host_submit(3, value, known, 0);
}

static void solve_level4(void) {
  u64 known = 0;
  u64 value = 0;
  int length;

  length = observe(4, L4_A, 16290);
  if (length > 0 && region_mask(scratch_ptr, length, 482, 90) == 0x0e9u) {
    known |= 0xf000000000000000ULL;
    value |= 0x5000000000000000ULL;
  }
  if (length > 0 && region_mask(scratch_ptr, length, 4510, 32) == 0x0f1u) {
    known |= 0x000000000000000fULL;
    value |= 0x0000000000000001ULL;
  }

  length = observe(4, L4_B, 16290);
  if (!(known & 0xf000000000000000ULL) && length > 0 &&
      region_mask(scratch_ptr, length, 481, 56) == 0x0e9u) {
    known |= 0xf000000000000000ULL;
    value |= 0xc000000000000000ULL;
  }
  if (!(known & 0x000000000000000fULL) && length > 0 &&
      region_mask(scratch_ptr, length, 4542, 107) == 0x0f1u) {
    known |= 0x000000000000000fULL;
    value |= 0x0000000000000009ULL;
  }

  length = observe(4, L4_C, 16290);
  if (!(known & 0xf000000000000000ULL) && length > 0 &&
      region_mask(scratch_ptr, length, 4311, 94) == 0x1e2u) {
    known |= 0xf000000000000000ULL;
    value |= 0xf000000000000000ULL;
  }

  length = observe(4, L4_E, 16290);
  if (!(known & 0x000000000000000fULL) && length > 0 &&
      region_mask(scratch_ptr, length, 4543, 127) == 0x1e2u) {
    known |= 0x0000000000000001ULL;
    value |= 0x0000000000000001ULL;
  }

  if (known) host_submit(4, value, known, 0);
}

__attribute__((visibility("default")))
void run_entry(void) {
  solve_level3();
  solve_level4();
  host_finalize();
}
