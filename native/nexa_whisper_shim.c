// nexa_whisper_shim.c
//
// A minimal, stable C API wrapping whisper.cpp's real (large, fragile)
// struct-based API, so the Python side only needs a handful of simple
// ctypes function bindings instead of hand-transcribing whisper_full_params
// (50+ fields) into ctypes.Structure, which is easy to get wrong.
//
// Compiled against the real whisper.h by the real C compiler, so the
// struct layout is always correct -- no ctypes struct guessing involved.

#include "whisper.h"
#include <stdlib.h>
#include <string.h>

// Loads the model once. Call nexa_whisper_free() when done with it.
struct whisper_context *nexa_whisper_init(const char *model_path) {
    struct whisper_context_params cparams = whisper_context_default_params();
    cparams.use_gpu = false; // CPU-only, matches the rest of Nexa's local-inference approach
    return whisper_init_from_file_with_params(model_path, cparams);
}

void nexa_whisper_free(struct whisper_context *ctx) {
    if (ctx) {
        whisper_free(ctx);
    }
}

// Transcribes mono 16kHz float32 PCM in [-1, 1]. Returns a malloc'd
// null-terminated UTF-8 string (caller must free via nexa_whisper_free_string),
// or NULL on failure.
//
// audio_ctx limits the encoder's context window (in frames, ~20ms each;
// 0 = full 1500-frame/30s context). Whisper's encoder always pads audio
// up to this context size internally regardless of actual input length,
// so a smaller audio_ctx directly cuts encoder compute for short live
// partial-preview calls -- pass 0 for the most accurate final result.
//
// initial_prompt (may be NULL) biases the decoder toward specific
// vocabulary/spellings -- used here so the tiny model reliably recognizes
// "Nexa" and Nexa's own command vocabulary instead of guessing near-miss
// words for them.
char *nexa_whisper_transcribe(struct whisper_context *ctx, const float *samples, int n_samples, int n_threads, int audio_ctx, const char *initial_prompt) {
    if (!ctx || !samples || n_samples <= 0) {
        return NULL;
    }

    struct whisper_full_params params = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
    params.print_progress   = false;
    params.print_special    = false;
    params.print_realtime   = false;
    params.print_timestamps = false;
    params.translate        = false;
    params.no_context       = true;
    params.single_segment   = false;
    params.language         = "en";
    params.n_threads        = n_threads > 0 ? n_threads : 4;
    params.audio_ctx        = audio_ctx;
    params.initial_prompt   = (initial_prompt && initial_prompt[0]) ? initial_prompt : NULL;

    int rc = whisper_full(ctx, params, samples, n_samples);
    if (rc != 0) {
        return NULL;
    }

    int n_segments = whisper_full_n_segments(ctx);
    size_t total_len = 1; // for the null terminator
    for (int i = 0; i < n_segments; i++) {
        const char *seg = whisper_full_get_segment_text(ctx, i);
        if (seg) {
            total_len += strlen(seg);
        }
    }

    char *result = (char *)malloc(total_len);
    if (!result) {
        return NULL;
    }
    result[0] = '\0';
    for (int i = 0; i < n_segments; i++) {
        const char *seg = whisper_full_get_segment_text(ctx, i);
        if (seg) {
            strcat(result, seg);
        }
    }
    return result;
}

void nexa_whisper_free_string(char *s) {
    if (s) {
        free(s);
    }
}
