# Builds and runs the board-independent unit tests on your PC (no ESP32 needed).
#   make -C test/host
CXX ?= g++
CXXFLAGS ?= -std=c++17 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined
LIB := ../../lib/bark_logic/src
SRCS := test_logic.cpp $(LIB)/trigger_policy.cpp $(LIB)/onset_detector.cpp $(LIB)/settings.cpp
BUILD := build

.PHONY: test clean
test: $(BUILD)/test_logic
	./$(BUILD)/test_logic

$(BUILD)/test_logic: $(SRCS) $(wildcard $(LIB)/*.h)
	mkdir -p $(BUILD)
	$(CXX) $(CXXFLAGS) -I$(LIB) $(SRCS) -o $@

clean:
	rm -rf $(BUILD)
