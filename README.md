# UH Mānoa Rover

A camera-only autonomous rover that learns to navigate the University of Hawaiʻi at Mānoa campus. It is trained in NVIDIA Isaac Sim on a reconstructed digital twin of the campus.

The full project scope (phases, architecture, deliverables, risks, and open decisions) is in **[docs/SCOPE.md](docs/SCOPE.md)**.

## The short version

1. **Capture**: photograph and video the campus, then georeference everything.
2. **Reconstruct**: build a clean 3D Gaussian-splat and mesh twin with people, cars, and animals removed.
3. **Simulate**: load the twin into Isaac Sim and add pedestrians, bikes, carts, cars, and the campus chickens and cats.
4. **Learn**: train camera-only perception, prediction, and crowd-aware navigation.
5. **Transfer**: move from sim to a real rover, starting on one short route and then expanding across campus.
