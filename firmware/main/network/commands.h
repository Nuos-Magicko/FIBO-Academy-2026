/**
 * @file commands.h
 * Commands the laptop can send to the board (see commands.cpp for the list).
 */
#pragma once
#include <cstdint>
#include "network/net.h"

/** Handles one COMMAND payload. Replies with PONG directly, otherwise with a cmd_ok / cmd_error EVENT. */
void command_dispatch(const uint8_t *payload, uint32_t len, client_connection_t *client);
