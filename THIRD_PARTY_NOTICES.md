# Third-party notices

## KLineChart Pro — Apache License 2.0

`frontend/src/chart/overlays/extra.ts` contains drawing tools (arrow, circle, rectangle, triangle, parallelogram,
Fibonacci tools, Gann box, waves, ABCD / XABCD patterns) ported from
[KLineChart Pro](https://github.com/klinecharts/pro), Copyright the KLineChart authors, licensed under the Apache
License, Version 2.0. Full text: [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt).

Changes made here: adapted to the KLineChart v10 overlay API, price precision taken from the chart symbol, fill
colours taken from the chart theme. This file stays under the Apache License 2.0; the rest of the project is MIT
(see `LICENSE`).

## Dependencies

Libraries installed through npm and pip (KLineChart, React, FastAPI and others) are not copied into this repository;
each keeps its own license, listed in its package metadata.
