/**
 * @file net.h
 * The net task: the board's TCP connection to the laptop (see net.cpp).
 */
#pragma once
#include <cstdint>
#include "cJSON.h"

/** The one TCP client the net task serves. */
struct client_connection_t {
    int sock;
    /** Record counter, restarted for each connection. */
    uint32_t seq;
    bool grid_requested, hello_requested, reboot_requested;
};

void net_task(void *);
/** HELLO: the board's details, software versions and current settings. */
cJSON *hello_json();
/** Sends obj as a JSON record and frees it. Returns false on a socket error. */
bool send_json(client_connection_t *client, uint8_t type, cJSON *obj);
