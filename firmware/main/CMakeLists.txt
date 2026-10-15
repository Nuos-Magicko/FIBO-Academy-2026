idf_component_register(SRCS "main.cpp"
                       INCLUDE_DIRS "."
                       REQUIRES pedestrian_detect esp-dl esp32-camera esp_driver_tsens
                                esp_psram spi_flash esp_timer esp_wifi esp_netif nvs_flash lwip
                       EMBED_FILES "pedestrian.jpg")
