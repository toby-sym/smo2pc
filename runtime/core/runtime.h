/* Guest CPU state and the helpers shared by the lifted game code (recomp.py's output) and the host runtime. */
#ifndef RECOMP_RUNTIME_H
#define RECOMP_RUNTIME_H

#include <stdint.h>
#include <string.h>
#include <math.h>
#include <intrin.h>

typedef union {
    uint8_t b[16];
    uint16_t h[8];
    uint32_t w[4];
    uint64_t d[2];
    float f[4];
    double df[2];
} V128;

typedef struct {
    uint64_t x[32];          /* x[31] is SP */
    V128 v[32];
    uint32_t N, Z, C, V;
    uint64_t tpidr;
    uint64_t ex_addr, ex_val, ex_val2; /* exclusive monitor: address and values seen by the last LDXR/LDXP */
    uint32_t ex_size;            /* reserved byte count; distinguishes scalar and pair exclusives */
    uint64_t ex_fail;            /* consecutive failed STXR, for diagnostics */
    uint64_t fn;                 /* entry address of the lifted function entered most recently */
    const char *in_import;       /* name of the host import currently executing on this context, or NULL */
    uint64_t import_from;        /* guest return address of that import call */
} Ctx;

void call_indirect(Ctx *c, uint64_t addr);
void host_import(Ctx *c, int sym);          /* call to a symbol another module provides */
typedef struct { uint64_t addr; void (*fn)(Ctx *); } FnEntry;
typedef struct { const char *name; uint64_t value; int defined; } DynSym;
extern const FnEntry fn_table[];
extern const int fn_table_count;
extern const DynSym dyn_syms[];
extern const int dyn_sym_count;
void guest_trap(const char *what, uint64_t addr);
void guest_unimpl(uint64_t pc, uint32_t insn, const char *what);
void guest_svc(Ctx *c, uint32_t number);

/* Guest memory is mapped at its real address: a guest pointer is a host pointer. */
#define DEF_MEM(bits) \
    static inline uint##bits##_t rd##bits(uint64_t a) { uint##bits##_t v; memcpy(&v, (void *)(uintptr_t)a, sizeof v); return v; } \
    static inline void wr##bits(uint64_t a, uint##bits##_t v) { memcpy((void *)(uintptr_t)a, &v, sizeof v); }
DEF_MEM(8) DEF_MEM(16) DEF_MEM(32) DEF_MEM(64)

/* STXR: store only if the location still holds the value the matching LDXR read. Returns 0 on success. */
void stx_diag(Ctx *c, uint64_t a, uint64_t expected, uint64_t seen, uint64_t v, int bits);
#define DEF_STX(bits) \
    static inline uint32_t stx##bits(Ctx *c, uint64_t a, uint##bits##_t v) { \
        if (c->ex_addr != a || c->ex_size != bits / 8) { c->ex_addr = ~0ull; c->ex_size = 0; stx_diag(c, a, 0, 0, v, -bits); return 1; } \
        c->ex_addr = ~0ull; \
        c->ex_size = 0; \
        if (__sync_bool_compare_and_swap((uint##bits##_t *)(uintptr_t)a, (uint##bits##_t)c->ex_val, v)) { c->ex_fail = 0; return 0; } \
        uint##bits##_t seen; memcpy(&seen, (void *)(uintptr_t)a, sizeof seen); \
        stx_diag(c, a, c->ex_val, seen, v, bits); \
        return 1; \
    }
DEF_STX(8) DEF_STX(16) DEF_STX(32) DEF_STX(64)

/* Pair exclusives use a single atomic snapshot and compare/exchange, matching the guest access width. The
   128-bit variant relies on CMPXCHG16B, which is part of the Windows 10 x64 minimum CPU requirements. */
static inline void ldxp32(Ctx *c, uint64_t a, uint32_t *lo, uint32_t *hi) {
    uint64_t v = __atomic_load_n((uint64_t *)(uintptr_t)a, __ATOMIC_SEQ_CST);
    c->ex_addr = a; c->ex_size = 8; c->ex_val = (uint32_t)v; c->ex_val2 = (uint32_t)(v >> 32);
    *lo = (uint32_t)v; *hi = (uint32_t)(v >> 32);
}
static inline void ldxp64(Ctx *c, uint64_t a, uint64_t *lo, uint64_t *hi) {
    __int64 v[2] = {0, 0};
    _InterlockedCompareExchange128((volatile __int64 *)(uintptr_t)a, 0, 0, v);
    c->ex_addr = a; c->ex_size = 16; c->ex_val = (uint64_t)v[0]; c->ex_val2 = (uint64_t)v[1];
    *lo = c->ex_val; *hi = c->ex_val2;
}
static inline uint32_t stxp32(Ctx *c, uint64_t a, uint32_t lo, uint32_t hi) {
    uint64_t expected = (uint32_t)c->ex_val | ((uint64_t)(uint32_t)c->ex_val2 << 32);
    uint64_t value = (uint64_t)lo | ((uint64_t)hi << 32);
    if (c->ex_addr != a || c->ex_size != 8) {
        c->ex_addr = ~0ull; c->ex_size = 0; stx_diag(c, a, 0, 0, value, -64); return 1;
    }
    c->ex_addr = ~0ull; c->ex_size = 0;
    if (__sync_bool_compare_and_swap((uint64_t *)(uintptr_t)a, expected, value)) { c->ex_fail = 0; return 0; }
    uint64_t seen = __sync_val_compare_and_swap((uint64_t *)(uintptr_t)a, expected, expected);
    stx_diag(c, a, expected, seen, value, 64);
    return 1;
}
static inline uint32_t stxp64(Ctx *c, uint64_t a, uint64_t lo, uint64_t hi) {
    __int64 expected[2] = {(__int64)c->ex_val, (__int64)c->ex_val2};
    if (c->ex_addr != a || c->ex_size != 16) {
        c->ex_addr = ~0ull; c->ex_size = 0; stx_diag(c, a, 0, 0, lo, -128); return 1;
    }
    c->ex_addr = ~0ull; c->ex_size = 0;
    if (_InterlockedCompareExchange128((volatile __int64 *)(uintptr_t)a, (__int64)hi, (__int64)lo, expected)) {
        c->ex_fail = 0; return 0;
    }
    stx_diag(c, a, c->ex_val, (uint64_t)expected[0], lo, 128);
    return 1;
}

static inline V128 rdv(uint64_t a, int n) { V128 v; v.d[0] = 0; v.d[1] = 0; memcpy(&v, (void *)(uintptr_t)a, (size_t)n); return v; }
static inline void wrv(uint64_t a, const V128 *v, int n) { memcpy((void *)(uintptr_t)a, v, (size_t)n); }
static inline void vzero(V128 *v) { v->d[0] = 0; v->d[1] = 0; }

static inline uint64_t adds64(Ctx *c, uint64_t a, uint64_t b, uint32_t cin) {
    uint64_t r = a + b + cin;
    c->N = (uint32_t)(r >> 63);
    c->Z = r == 0;
    c->C = (r < a) || (cin && r == a);
    c->V = (uint32_t)(((a ^ r) & (b ^ r)) >> 63);
    return r;
}
static inline uint32_t adds32(Ctx *c, uint32_t a, uint32_t b, uint32_t cin) {
    uint64_t wide = (uint64_t)a + b + cin;
    uint32_t r = (uint32_t)wide;
    c->N = r >> 31;
    c->Z = r == 0;
    c->C = (uint32_t)(wide >> 32);
    c->V = ((a ^ r) & (b ^ r)) >> 31;
    return r;
}
static inline uint64_t logic64(Ctx *c, uint64_t r) { c->N = (uint32_t)(r >> 63); c->Z = r == 0; c->C = 0; c->V = 0; return r; }
static inline uint32_t logic32(Ctx *c, uint32_t r) { c->N = r >> 31; c->Z = r == 0; c->C = 0; c->V = 0; return r; }
static inline void set_nzcv(Ctx *c, int f) { c->N = (f >> 3) & 1; c->Z = (f >> 2) & 1; c->C = (f >> 1) & 1; c->V = f & 1; }

static inline int cond(const Ctx *c, int cc) {
    int r;
    switch (cc >> 1) {
    case 0: r = c->Z; break;
    case 1: r = c->C; break;
    case 2: r = c->N; break;
    case 3: r = c->V; break;
    case 4: r = c->C && !c->Z; break;
    case 5: r = c->N == c->V; break;
    case 6: r = c->N == c->V && !c->Z; break;
    default: return 1;
    }
    return (cc & 1) ? !r : r;
}

static inline void fcmp(Ctx *c, double a, double b) {
    if (a != a || b != b) set_nzcv(c, 3);
    else if (a == b) set_nzcv(c, 6);
    else if (a < b) set_nzcv(c, 8);
    else set_nzcv(c, 2);
}

static inline uint32_t ror32(uint32_t v, unsigned n) { n &= 31; return n ? (v >> n) | (v << (32 - n)) : v; }
static inline uint64_t ror64(uint64_t v, unsigned n) { n &= 63; return n ? (v >> n) | (v << (64 - n)) : v; }
static inline uint32_t bswap32(uint32_t v) { return __builtin_bswap32(v); }
static inline uint64_t bswap64(uint64_t v) { return __builtin_bswap64(v); }
static inline uint64_t rev32_64(uint64_t v) { return ((uint64_t)bswap32((uint32_t)(v >> 32)) << 32) | bswap32((uint32_t)v); }
static inline uint32_t rev16_32(uint32_t v) { return ((v & 0xff00ff00u) >> 8) | ((v & 0x00ff00ffu) << 8); }
static inline uint64_t rev16_64(uint64_t v) { return ((v & 0xff00ff00ff00ff00ull) >> 8) | ((v & 0x00ff00ff00ff00ffull) << 8); }
static inline uint32_t clz32(uint32_t v) { return v ? (uint32_t)__builtin_clz(v) : 32; }
static inline uint64_t clz64(uint64_t v) { return v ? (uint64_t)__builtin_clzll(v) : 64; }
static inline uint32_t cls32(uint32_t v) { return clz32(((v >> 1) ^ v) & 0x7fffffffu) - 1; }
static inline uint64_t cls64(uint64_t v) { return clz64(((v >> 1) ^ v) & 0x7fffffffffffffffull) - 1; }
static inline uint32_t rbit32(uint32_t v) { uint32_t r = 0; for (int i = 0; i < 32; i++) r |= ((v >> i) & 1u) << (31 - i); return r; }
static inline uint64_t rbit64(uint64_t v) { uint64_t r = 0; for (int i = 0; i < 64; i++) r |= ((v >> i) & 1ull) << (63 - i); return r; }
static inline uint64_t umulh(uint64_t a, uint64_t b) { return (uint64_t)(((unsigned __int128)a * b) >> 64); }
static inline uint64_t smulh(uint64_t a, uint64_t b) { return (uint64_t)(((__int128)(int64_t)a * (int64_t)b) >> 64); }
static inline uint32_t sdiv32(uint32_t a, uint32_t b) {
    if (b == 0) return 0;
    if (a == 0x80000000u && b == 0xffffffffu) return a;
    return (uint32_t)((int32_t)a / (int32_t)b);
}
static inline uint64_t sdiv64(uint64_t a, uint64_t b) {
    if (b == 0) return 0;
    if (a == 0x8000000000000000ull && b == ~0ull) return a;
    return (uint64_t)((int64_t)a / (int64_t)b);
}

/* float -> integer with AArch64 saturation semantics */
static inline uint32_t f2s32(double v) { if (v != v) return 0; if (v >= 2147483647.0) return 0x7fffffffu; if (v <= -2147483648.0) return 0x80000000u; return (uint32_t)(int32_t)v; }
static inline uint32_t f2u32(double v) { if (v != v || v <= 0) return 0; if (v >= 4294967295.0) return 0xffffffffu; return (uint32_t)v; }
static inline uint64_t f2s64(double v) { if (v != v) return 0; if (v >= 9223372036854775807.0) return 0x7fffffffffffffffull; if (v <= -9223372036854775808.0) return 0x8000000000000000ull; return (uint64_t)(int64_t)v; }
static inline uint64_t f2u64(double v) { if (v != v || v <= 0) return 0; if (v >= 18446744073709551615.0) return ~0ull; return (uint64_t)v; }

#include "fpexact.h"
#include "neon.h"

#endif
