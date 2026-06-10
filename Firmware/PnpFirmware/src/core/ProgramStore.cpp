#include "ProgramStore.h"
#include "../platform/Platform.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

namespace pnp {

// ============================================================
// beginTransfer  — malloc the exact buffer we need
// ============================================================

ProgramStore::Result ProgramStore::beginTransfer(uint32_t size, uint16_t chunks) {
    if (size == 0 || chunks == 0) return Result::InvalidParam;
    if (size > kMaxProgramBytes)  return Result::BufferFull;

    // Free any leftover buffer from a previous (possibly failed) transfer.
    if (buf_) { free(buf_); buf_ = nullptr; }

    // +1 for null terminator added before JSON parse.
    buf_ = (char*)malloc(size + 1);
    if (!buf_) return Result::BufferFull;   // OOM

    bufLen_        = 0;
    xferSize_      = size;
    xferChunks_    = chunks;
    xferReceived_  = 0;
    programLoaded_ = false;
    return Result::Ok;
}

// ============================================================
// receiveChunk  — base64 decode into heap buffer
// ============================================================

uint8_t ProgramStore::b64Val(char c) {
    if (c >= 'A' && c <= 'Z') return (uint8_t)(c - 'A');
    if (c >= 'a' && c <= 'z') return (uint8_t)(c - 'a' + 26);
    if (c >= '0' && c <= '9') return (uint8_t)(c - '0' + 52);
    if (c == '+') return 62;
    if (c == '/') return 63;
    if (c == '=') return 0;
    return 255;
}

ProgramStore::Result ProgramStore::receiveChunk(uint16_t index,
                                                 const char* b64Data) {
    if (xferSize_ == 0 || !buf_) return Result::NoTransferInProgress;
    if (index != xferReceived_)  return Result::OutOfOrder;
    if (!b64Data)                return Result::BadBase64;

    const char* p = b64Data;
    while (*p) {
        if (!*(p+1) || !*(p+2) || !*(p+3)) return Result::BadBase64;
        uint8_t a = b64Val(p[0]), b = b64Val(p[1]),
                c = b64Val(p[2]), d = b64Val(p[3]);
        if (a == 255 || b == 255 || c == 255 || d == 255) return Result::BadBase64;

        if (bufLen_ >= xferSize_) return Result::BufferFull;
        buf_[bufLen_++] = (char)((a << 2) | (b >> 4));
        if (p[2] != '=') {
            if (bufLen_ >= xferSize_) return Result::BufferFull;
            buf_[bufLen_++] = (char)((b << 4) | (c >> 2));
        }
        if (p[3] != '=') {
            if (bufLen_ >= xferSize_) return Result::BufferFull;
            buf_[bufLen_++] = (char)((c << 6) | d);
        }
        p += 4;
    }
    xferReceived_++;
    return Result::Ok;
}

// ============================================================
// endTransfer  — parse, validate, then FREE the raw buffer
// ============================================================

ProgramStore::Result ProgramStore::endTransfer(char* errOut, size_t errLen) {
    errOut[0] = '\0';

    if (xferSize_ == 0 || !buf_) {
        PNP_SNPRINTF(errOut, errLen, "no_transfer_in_progress");
        return Result::NoTransferInProgress;
    }
    if (xferReceived_ != xferChunks_) {
        PNP_SNPRINTF(errOut, errLen, "incomplete_%u_of_%u",
                     (unsigned)xferReceived_, (unsigned)xferChunks_);
        free(buf_); buf_ = nullptr; xferSize_ = 0;
        return Result::Incomplete;
    }
    if (bufLen_ != xferSize_) {
        PNP_SNPRINTF(errOut, errLen, "size_mismatch");
        free(buf_); buf_ = nullptr; xferSize_ = 0;
        return Result::SizeMismatch;
    }

    buf_[bufLen_] = '\0';
    storedBytes_  = bufLen_;
    xferSize_     = 0;

    // Parse. Passing const char* forces ArduinoJson to COPY all strings,
    // so the document is self-contained and buf_ can be freed immediately.
    doc_.clear();
    DeserializationError jerr = deserializeJson(doc_, (const char*)buf_);
    free(buf_); buf_ = nullptr; bufLen_ = 0;    // ← free as soon as parsed

    if (jerr) {
        PNP_SNPRINTF(errOut, errLen, "json_error:%s", jerr.c_str());
        storedBytes_ = 0;
        return Result::JsonError;
    }

    // Validate against the already-parsed document (no second JSON parse).
    if (!validator_.validate(doc_.as<JsonObjectConst>(), errOut, errLen)) {
        doc_.clear(); storedBytes_ = 0;
        return Result::ValidationError;
    }

    programLoaded_ = true;
    return Result::Ok;
}

// ============================================================
// Helpers
// ============================================================

void ProgramStore::reset() {
    if (buf_) { free(buf_); buf_ = nullptr; }
    bufLen_        = 0;
    storedBytes_   = 0;
    xferSize_      = 0;
    xferChunks_    = 0;
    xferReceived_  = 0;
    programLoaded_ = false;
    doc_.clear();
}

int ProgramStore::instructionCount() const {
    if (!programLoaded_) return 0;
    return (int)doc_["program"].as<JsonArrayConst>().size();
}

}  // namespace pnp
