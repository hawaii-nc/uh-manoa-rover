# What UH's photos of the route show, and where the model uses it

Read on 2026-11-16 from the photos on UH's campus map (`https://map.hawaii.edu/images/MA/building_images/<number>_<nn>.jpg`):
Holmes Hall (1163, 4 photos), Campus Center (1055, 5), Architecture (1016, 4). The photos are UH's, so they stay in
`sites/uh_manoa/cache/photos/` (not in git). Wikimedia Commons has almost nothing at path level for this route
(a plaque, a few trees, a newspaper box), so UH's photos are the only route-level source.

| Photo | What it shows | Used for |
|---|---|---|
| 1163_01, _02 | Dense rows of tall coconut palms along Holmes Hall; light concrete frame, deep balconies, open ground floor, lawn, a large orange steel sculpture | Coconut palm shape (curved slender trunk, drooping pinnate fronds), Holmes style sheet |
| 1163_03 | Shade trees with pale new growth beside the building; screwpine (hala) at the left; grey-green board-formed infill panels | Screwpine and broadleaf shapes, panel colour |
| 1163_04 | Large grey concrete slabs with gravel strips between them, a parking lot, a street lamp with a drum shade | Slab joints every 3 m in the concrete texture; drum lamp |
| 1055_01, _04 | Campus Center plaza: tan brick pavers in running bond with a curved pale band, a lawn of round stepping stones, wood-slat benches with steel frames, a huge monkeypod with a flat umbrella crown, green terrace stairs with railings, round drain covers | Brick paving region (`paving.yaml`), monkeypod shape, stepping stones (already surveyed), benches |
| 1055_03 | Monkeypod from the side with white slotted drum trash cans and slim lamp poles with globes | Trash can and monkeypod shapes |
| 1055_05 | Tall bamboo hedge, short-cut lawn that browns in patches, green terrace stairs, a bicycle on a rack | Hedge and lawn colour |
| 1016_01, _03, _04 | Architecture: cream stucco with pilasters, green-framed windows, brown base band, entrance stairs with handrails, a thin grey path across the lawn, picnic tables, ti plants, ferns and grasses along the base, dark-bronze drum-shade lamp poles | Style sheet, ti/fern/grass ground cover, drum lamp |
| 1016_02 | Rain tree with pale pink flowers in front of the curved courtyard wall | Pink flecks on rain-tree and shower-tree leaves |

## What is read from photos and what is not

- **Positions** always come from UH's surveyed layers (trees, planter beds, light poles, benches). Photos only decide
  the *look* of things at those positions.
- **Trees** take their shape from the species UH records (coconut, monkeypod, banyan, plumeria, ...). Species the photos
  do not show get a neutral spreading crown.
- **Ground cover** (ti, fern, grass tufts, low bush) fills the surveyed planter beds within 100 m of the route in the
  mix the photos show at the three route buildings. It is an assumption for planters the photos do not show.
- **Brick paving** covers a circle (`sites/uh_manoa/paving.yaml`) around the Campus Center plaza; the true extent is
  only roughly visible, so it is listed as approximate. Check it in the explorer.
- **Leaf colours and flower flecks** are an artist's reading of the photos, not measurements.
- **Fence heights, zebra bars, lamp shape** are typical values (see WORLD.md).


## Sculptures and street furniture (UH art layer and photos)

UH's art layer gives each piece a name, artist, year, surveyed point and a photo
(`https://map.hawaii.edu/images/MA/Art/ART_0nn.jpg`, cached under `sites/uh_manoa/cache/photos/art/`).
`monuments.py` draws the outdoor pieces nearest the route at those points:

| Piece (artist, year) | Where | What the photo shows | Size basis |
|---|---|---|---|
| Gate of Hope (Liberman, 1972) | Holmes Hall lawn | red-orange painted steel cylinders cut on the slant | estimate, about 6 m |
| Divers (Stackhouse, 1991) | lawn east of Holmes | a ribbed brass boat frame lying on the grass | estimate, about 7 m long |
| Krypton 1 x 6 x 18 (Hopper, 1973) | under the trees by Kuykendall | a pale concrete monolith | the title: 1 x 6 x 18 ft |
| The Fourth Sign (Smith, 1976) | north of the plaza | two black angular steel arches | estimate |
| Hawai‘i Peace Memorial (Koji, 1986) | east lawn | a five-tier granite pagoda | estimate |
| To the Nth Power (Watson, 1971) | near Architecture | five interlocked weathering-steel rings | estimate |
| Hina o na Lani (Clurman, 1975) | near Architecture | a seated pink granite figure on a black plinth | estimate |
| Instructional Steel Connection Sculpture (AISC, 2004) | Holmes Hall | bolted steel beam connections | estimate |
| Spirit of Loyalty, Mind & Heart (1995) | Varsity Victory Volunteers court | a green spire and a twisting bronze flame | estimate |
| Chance Meeting (Segal, 1991) | by the street | three life-size figures in long coats | life size |
| Varney Circle Fountain (1934) | south lawn | a round concrete fountain inside a round hedge | estimate |

Murals and indoor pieces are not drawn. UH's seating layer says **Bench** or **Table**: benches are wood-slat on
steel frames (Campus Center photos), tables are picnic tables (Architecture photos). The green terrace stairs and
the stair handrails at Campus Center and Architecture come from photos 1055_01/_05 and 1016_01/_03/_04; the survey
maps neither the colour nor the rails.
