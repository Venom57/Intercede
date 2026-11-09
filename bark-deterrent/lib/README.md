# lib/

- `bark_logic/` holds the board-independent code (trigger policy, sound gate,
  sweep math, settings). It's unit tested on a PC with `make -C test/host`.
- Put your **Edge Impulse model** here. In Edge Impulse go to
  *Deployment → Arduino library → Build* and unzip the download into this
  folder, so you end up with `lib/bark_detector_inferencing/`. The firmware
  picks it up automatically on the next build. With no model here, it uses
  the placeholder detector.

  If your project isn't named `bark_detector`, the folder and header have a
  different name. Either rename the project in Edge Impulse and export again,
  or change the two `bark_detector_inferencing.h` lines at the top of
  `src/detector.cpp`.
