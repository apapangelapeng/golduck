#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define BUFFER_SIZE (1u << 20)
#define FNV_OFFSET UINT64_C(14695981039346656037)
#define FNV_PRIME UINT64_C(1099511628211)

static uint64_t hash_u32(uint64_t hash, uint32_t value) {
  for (int shift = 0; shift < 32; shift += 8) {
    hash ^= (value >> shift) & 255u;
    hash *= FNV_PRIME;
  }
  return hash;
}

int main(int argc, char **argv) {
  if (argc != 4 && argc != 8) {
    fprintf(stderr,
            "usage: hash_tiled_views GRID PITCH PADDING [WIDTH HEIGHT DX DY]\n");
    return 2;
  }
  const int grid = atoi(argv[1]);
  const int pitch = atoi(argv[2]);
  const int padding = atoi(argv[3]);
  const int view_width = argc == 8 ? atoi(argv[4]) : 1000;
  const int view_height = argc == 8 ? atoi(argv[5]) : 200;
  const int view_dx = argc == 8 ? atoi(argv[6]) : 0;
  const int view_dy = argc == 8 ? atoi(argv[7]) : 0;
  if (grid <= 0 || pitch <= 0 || padding <= 0 ||
      view_width <= 0 || view_height <= 0) return 2;

  const int count = grid * grid;
  uint64_t *hashes = malloc((size_t)count * sizeof(*hashes));
  if (!hashes) return 2;
  for (int index = 0; index < count; ++index) hashes[index] = FNV_OFFSET;

  unsigned char *buffer = malloc(BUFFER_SIZE);
  if (!buffer) return 2;
  uint64_t repeat = 0;
  int x = 0;
  int y = 0;
  int header = 1;
  int ended = 0;

  while (!ended) {
    size_t length = fread(buffer, 1, BUFFER_SIZE, stdin);
    if (!length) break;
    for (size_t offset = 0; offset < length; ++offset) {
      unsigned char ch = buffer[offset];
      if (header) {
        if (ch == '\n') header = 0;
        continue;
      }
      if (ch >= '0' && ch <= '9') {
        repeat = repeat * 10u + (unsigned)(ch - '0');
        continue;
      }
      if (ch == '\n' || ch == '\r' || ch == ' ' || ch == '\t') continue;

      int run = repeat ? (int)repeat : 1;
      repeat = 0;
      if (ch == 'o') {
        int view_top_zero = padding + view_dy - view_height / 2;
        int row = (y - view_top_zero) / pitch;
        if (y < view_top_zero) row = -1;
        if (row >= 0 && row < grid &&
            y >= view_top_zero + row * pitch &&
            y < view_top_zero + view_height + row * pitch) {
          for (int column = 0; column < grid; ++column) {
            int view_left = padding + view_dx - view_width / 2 + column * pitch;
            int start = x > view_left ? x : view_left;
            int end = x + run < view_left + view_width
                          ? x + run : view_left + view_width;
            if (start < end) {
              int index = row * grid + column;
              uint64_t hash = hashes[index];
              hash = hash_u32(hash,
                              (uint32_t)(y - (view_top_zero + row * pitch)));
              hash = hash_u32(hash, (uint32_t)(start - view_left));
              hash = hash_u32(hash, (uint32_t)(end - view_left));
              hashes[index] = hash;
            }
          }
        }
        x += run;
      } else if (ch == 'b') {
        x += run;
      } else if (ch == '$') {
        y += run;
        x = 0;
      } else if (ch == '!') {
        ended = 1;
        break;
      }
    }
  }

  for (int index = 0; index < count; ++index)
    printf("%d %016llx\n", index, (unsigned long long)hashes[index]);
  free(buffer);
  free(hashes);
  return 0;
}
