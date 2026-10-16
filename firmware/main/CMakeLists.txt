idf_component_register(SRCS "main.cpp"
                            "config/settings.cpp"
                            "vision/vision.cpp" "vision/camera.cpp" "vision/grid.cpp"
                            "network/net.cpp" "network/wifi.cpp" "network/protocol.cpp" "network/commands.cpp"
                            "telemetry/metrics.cpp" "telemetry/events.cpp" "telemetry/thermal.cpp"
                            "utils/device.cpp"
                       INCLUDE_DIRS "."
                       REQUIRES pedestrian_detect esp-dl esp32-camera esp_driver_tsens esp_psram spi_flash
                                esp_timer esp_wifi esp_netif nvs_flash lwip json esp_app_format
                       EMBED_FILES "assets/pedestrian.jpg")

# Component versions for HELLO, read from the managed components actually used in this build.
foreach(pair "ESP_DL_VER;espressif__esp-dl" "ESP32_CAMERA_VER;espressif__esp32-camera"
             "PEDESTRIAN_DETECT_VER;espressif__pedestrian_detect")
    list(GET pair 0 def)
    list(GET pair 1 dir)
    file(STRINGS "${CMAKE_SOURCE_DIR}/managed_components/${dir}/idf_component.yml" line REGEX "^version:")
    string(REGEX REPLACE "^version: *\"?([^\"]*)\"?.*" "\\1" ver "${line}")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE ${def}="${ver}")
endforeach()
