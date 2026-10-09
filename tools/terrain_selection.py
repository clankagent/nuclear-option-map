"""Selection of serialized base surface mesh families; overlays stay separate."""
import re


def terrain_record(record):
    return re.fullmatch(r"terrain2_tile\d+|terrain_naval_island\d+", record["name"], re.I) is not None


def ground_surface_record(record):
    """Referenced terrain, fields, city ground and airfield base surfaces."""
    return terrain_record(record) or re.fullmatch(
        r"terrain2_citybase3|terrain2_city[12]_base|terrain2_fields[1235]|fields4|"
        r"airbase1|airbase_mountains|airbase_city|airbase_boscali_north|"
        r"terrain_naval_island\d+_(?:cityblocks|blocks)",record["name"],re.I) is not None


def building_surface_record(record):
    return re.match(r"(?:residential_|commercial_|suburb_block|industrial_|hangar_|factory_|controlTower|midrise_|storageTank|shelter|ammunitionBunker|Helipad|revetment)",
                    record["name"],re.I) is not None


def road_surface_record(record):
    return re.match(r"terrain2_roads|terrain_naval_island\d+_(?:roads|decalRoads)",record["name"],re.I) is not None
