typedef unsigned long long u64;

__attribute__((import_module("env"), import_name("run")))
int host_run(int level, const char *rle, int length, int generations, char *out);

__attribute__((import_module("env"), import_name("submit")))
int host_submit(int level, u64 value, u64 known_mask, u64 guess_mask);

__attribute__((import_module("env"), import_name("finalize")))
int host_finalize(void);

#define SCRATCH_CAPACITY (1024 * 1024)
char scratch_ptr[SCRATCH_CAPACITY];
char scratch_cap;

/* Two phases/lanes of a northbound p7 loafer, aimed at the last glyph. */
static const char LOAFER_A[] =
    "x = 2000, y = 100, rule = B3/S23\n"
    "1082bo$1081bobo$1080bo2bo$1081b2o2$1077bo5bo$"
    "1076bobo3bo$1075bo2bo3b2o$1075bo2b2o3bo!";

static const char LOAFER_B[] =
    "x = 2000, y = 100, rule = B3/S23\n"
    "1080bo$1079bobo$1079bo2bo$1080b2o2$1079bo5bo$"
    "1080bo3bobo$1079b2o3bo2bo$1079bo3b2o2bo!";

typedef struct {
  unsigned short x;
  unsigned char y;
  unsigned short mask;
  unsigned char digit;
} Feature;

/* Exact isolated components learned from glyph reactions, not from seeds. */
static const Feature FEATURES_A[] = {
    {139, 187, 0x19c, 0x7},
    {215, 107, 0x0e9, 0x4},
    {228,  82, 0x0e9, 0xd},
    {870, 112, 0x0b5, 0x1},
    {289,  84, 0x0e9, 0x1},
    {290,  19, 0x09d, 0x1},
    {191,  89, 0x09d, 0x1},
    {989, 154, 0x1ac, 0x7},
    {978, 102, 0x0b5, 0x7},
};

static const Feature FEATURES_B[] = {
    {164, 172, 0x0e9, 0x5},
    {130, 179, 0x09d, 0x7},
    {231, 116, 0x09d, 0x2},
    {109, 168, 0x19c, 0x7},
    {924,  34, 0x1e2, 0x7},
    {104, 179, 0x09d, 0xa},
    {109, 180, 0x0e9, 0x8},
};

static int string_length(const char *text) {
  int length = 0;
  while (text[length]) ++length;
  return length;
}

/* Read a square, at most 5x5, from a viewing-window RLE. */
static unsigned region_mask(const char *rle, int length,
                            int box_x, int box_y, int size) {
  int index = 0;
  int x = 0;
  int y = 0;
  unsigned repeat = 0;
  unsigned mask = 0;

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
      if (ch == 'o' && y >= box_y && y < box_y + size) {
        int first = x > box_x ? x : box_x;
        int last = x + (int)run < box_x + size
                       ? x + (int)run : box_x + size;
        for (int live_x = first; live_x < last; ++live_x) {
          unsigned bit = (unsigned)((y - box_y) * size + live_x - box_x);
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
  }
  return mask;
}

/* Embed a 3x3 feature in the centre of a 5x5 empty guard box. */
static unsigned guarded_mask(unsigned mask3) {
  unsigned result = 0;
  for (unsigned bit = 0; bit < 9; ++bit) {
    if (mask3 & (1u << bit)) {
      unsigned dx = bit % 3u;
      unsigned dy = bit / 3u;
      result |= 1u << ((dy + 1u) * 5u + dx + 1u);
    }
  }
  return result;
}

static int feature_present(const Feature *feature, int length) {
  return region_mask(scratch_ptr, length,
                     (int)feature->x - 1, (int)feature->y - 1, 5) ==
         guarded_mask(feature->mask);
}

static void merge_features(const Feature *features, int count, int length,
                           int *candidate, int *conflict) {
  if (length <= 0) return;
  for (int index = 0; index < count; ++index) {
    if (!feature_present(&features[index], length)) continue;
    int digit = (int)features[index].digit;
    if (*candidate < 0) *candidate = digit;
    else if (*candidate != digit) *conflict = 1;
  }
}

__attribute__((visibility("default")))
void run_entry(void) {
  int candidate = -1;
  int conflict = 0;

  int length = host_run(3, LOAFER_A, string_length(LOAFER_A),
                        7100, scratch_ptr);
  merge_features(FEATURES_A,
                 (int)(sizeof(FEATURES_A) / sizeof(FEATURES_A[0])),
                 length, &candidate, &conflict);

  length = host_run(3, LOAFER_B, string_length(LOAFER_B),
                    7100, scratch_ptr);
  merge_features(FEATURES_B,
                 (int)(sizeof(FEATURES_B) / sizeof(FEATURES_B[0])),
                 length, &candidate, &conflict);

  if (candidate >= 0 && !conflict)
    host_submit(3, (u64)candidate, 0xFULL, 0);
  host_finalize();
}
