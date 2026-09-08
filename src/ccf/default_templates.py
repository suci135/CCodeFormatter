"""Default C templates shipped inside the application package.

The examples are deliberately complete enough to act as a formatting
reference: include guards, conditional compilation, macros, declarations,
types, callbacks, control flow, comments, and function definitions are all
represented without relying on files outside the application.
"""

from __future__ import annotations


DEFAULT_TEMPLATES = {
    ".c": r'''/*
 * File: template.c
 * Description: C source-file formatting reference.
 */

#include "template.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(CCF_ENABLE_LOGGING)
#define CCF_LOG(message) ((void)printf("[ccf] %s\n", (message)))
#else
#define CCF_LOG(message) CCF_UNUSED(message)
#endif

#if defined(_WIN32)
#define CCF_PLATFORM_NAME "windows"
#elif defined(__linux__)
#define CCF_PLATFORM_NAME "linux"
#else
#define CCF_PLATFORM_NAME "other"
#endif

/* Global definitions. */
const char *const ccf_name = "C formatter template";
int32_t ccf_global_value = 0;

/* Private helpers. */

/* Validate a buffer before it is used. */
static int ccf_is_valid_buffer(const ccf_buffer_t *buffer)
{
    if (buffer == NULL || buffer->data == NULL)
    {
        return 0;
    }

    return buffer->length <= buffer->capacity;
}

/* Clear a byte array before handing it to the caller. */
static void ccf_clear_bytes(uint8_t *data, size_t length)
{
    size_t index;

    if (data == NULL)
    {
        return;
    }

    for (index = 0U; index < length; ++index)
    {
        data[index] = 0U;
    }
}

/* Public API implementations. */

/* Initialize a buffer and allocate its backing storage. */
ccf_status_t ccf_init(ccf_buffer_t *buffer, size_t capacity)
{
    if (buffer == NULL || capacity == 0U)
    {
        return CCF_ERROR; /* Invalid input. */
    }

    buffer->data = malloc(capacity);
    if (buffer->data == NULL)
    {
        return CCF_ERROR;
    }

    buffer->length = 0U;
    buffer->capacity = capacity;
    ccf_clear_bytes(buffer->data, capacity);
    return CCF_OK;
}

/* Release all storage owned by a buffer. */
void ccf_reset(ccf_buffer_t *buffer)
{
    if (buffer == NULL)
    {
        return;
    }

    free(buffer->data);
    buffer->data = NULL;
    buffer->length = 0U;
    buffer->capacity = 0U;
}

/* Append a byte range to a buffer after validating its capacity. */
ccf_status_t ccf_write(ccf_buffer_t *buffer, const void *data, size_t length)
{
    if (!ccf_is_valid_buffer(buffer) || data == NULL)
    {
        return CCF_ERROR;
    }

    if (length > buffer->capacity - buffer->length)
    {
        return CCF_ERROR;
    }

    memcpy(buffer->data + buffer->length, data, length);
    buffer->length += length;
    return CCF_OK;
}

/* Add two signed 32-bit values. */
int32_t ccf_add(int32_t left, int32_t right)
{
    return left + right;
}

/* Compare two points and return non-zero when they are equal. */
int ccf_point_equal(ccf_point_t left, ccf_point_t right)
{
    return left.x == right.x && left.y == right.y;
}

/* Write a readable mode name into a caller-provided buffer. */
ccf_status_t ccf_mode_name(ccf_mode_t mode, char *name, size_t capacity)
{
    if (name == NULL || capacity == 0U)
    {
        return CCF_ERROR;
    }

    switch (mode)
    {
    case CCF_MODE_IDLE:
        (void)snprintf(name, capacity, "%s", "idle");
        break;
    case CCF_MODE_READ:
        (void)snprintf(name, capacity, "%s", "read");
        break;
    case CCF_MODE_WRITE:
        (void)snprintf(name, capacity, "%s", "write");
        break;
    default:
        (void)snprintf(name, capacity, "%s", "unknown");
        break;
    }

    return CCF_OK;
}

/* Process each non-zero byte and report callback failures to the caller. */
ccf_status_t ccf_process(ccf_buffer_t *buffer, ccf_callback_t callback, void *context)
{
    size_t index;

    if (!ccf_is_valid_buffer(buffer) || callback == NULL)
    {
        return CCF_ERROR;
    }

    index = 0U;
    while (index < buffer->length)
    {
        if (buffer->data[index] != 0U)
        {
            if (callback("processing", context) != CCF_OK)
            {
                return CCF_ERROR;
            }
        }

        ++index;
    }

    do
    {
        CCF_LOG("processing pass");
        ++index;
    }
    while (index < buffer->length);

    CCF_LOG("processing complete");
    return CCF_OK;
}

/* Example application entry point. */
int main(void)
{
    ccf_buffer_t buffer = {0};
    ccf_point_t origin = {.x = 0, .y = 0};
    ccf_point_t point = {1, 2};
    ccf_value_t value = {.integer = ccf_add(1, 2)};
    char mode_name[16] = {0};
    const char message[] = "hello";

    if (ccf_init(&buffer, 64U) != CCF_OK)
    {
        return EXIT_FAILURE;
    }

    if (ccf_write(&buffer, message, strlen(message)) != CCF_OK)
    {
        ccf_reset(&buffer);
        return EXIT_FAILURE;
    }

    if (ccf_mode_name(CCF_MODE_READ, mode_name, sizeof(mode_name)) != CCF_OK)
    {
        ccf_reset(&buffer);
        return EXIT_FAILURE;
    }

    printf("%s on %s: %d, %s, %d\n", ccf_name, CCF_PLATFORM_NAME, value.integer, mode_name, ccf_point_equal(origin, point));
    ccf_reset(&buffer);
    return EXIT_SUCCESS;
}
''',
    ".h": r'''/*
 * File: template.h
 * Description: C header-file formatting reference.
 */

#ifndef CCF_TEMPLATE_H
#define CCF_TEMPLATE_H

#include <stddef.h>
#include <stdint.h>

/* Public configuration macros. */
#ifndef CCF_BUFFER_CAPACITY
#define CCF_BUFFER_CAPACITY 64U
#endif

#if defined(CCF_ENABLE_DIAGNOSTICS)
#define CCF_OK 0
#define CCF_ERROR (-1)
#else
#define CCF_OK 0
#define CCF_ERROR (-1)
#endif

#define CCF_VERSION_MAJOR 1U
#define CCF_VERSION_MINOR 0U
#define CCF_ARRAY_SIZE(array) (sizeof(array) / sizeof((array)[0]))
#define CCF_UNUSED(value) ((void)(value))

#if defined(CCF_ENABLE_DIAGNOSTICS)
#define CCF_DIAGNOSTICS_ENABLED 1U
#else
#define CCF_DIAGNOSTICS_ENABLED 0U
#endif

#ifdef __cplusplus
extern "C"
{
#endif

/* Public type definitions. */

typedef int ccf_status_t;

typedef enum ccf_mode
{
    CCF_MODE_IDLE = 0,
    CCF_MODE_READ = 1,
    CCF_MODE_WRITE = 2
} ccf_mode_t;

typedef struct ccf_point
{
    int32_t x;
    int32_t y;
} ccf_point_t;

typedef struct ccf_buffer
{
    uint8_t *data;
    size_t length;
    size_t capacity;
} ccf_buffer_t;

typedef union ccf_value
{
    int32_t integer;
    double decimal;
    void *pointer;
} ccf_value_t;

typedef ccf_status_t (*ccf_callback_t)(const char *message, void *context);

/* Public data and function declarations. */

extern const char *const ccf_name;
extern int32_t ccf_global_value;

ccf_status_t ccf_init(ccf_buffer_t *buffer, size_t capacity);
void ccf_reset(ccf_buffer_t *buffer);
ccf_status_t ccf_write(ccf_buffer_t *buffer, const void *data, size_t length);
int32_t ccf_add(int32_t left, int32_t right);
int ccf_point_equal(ccf_point_t left, ccf_point_t right);
ccf_status_t ccf_mode_name(ccf_mode_t mode, char *name, size_t capacity);
ccf_status_t ccf_process(ccf_buffer_t *buffer, ccf_callback_t callback, void *context);

#ifdef __cplusplus
}
#endif

#endif /* CCF_TEMPLATE_H */
''',
}
