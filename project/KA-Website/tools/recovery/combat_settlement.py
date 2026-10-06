"""Special-battle chest dispatch parameters, separate from world collection."""
from combat_resolution import f32,random_range


def treasure_flight(treasure_id,position,next_math):
    # FireTreasure14f0118 consumes these draws before CreateTreasure.
    duration=random_range(next_math('treasure_flight_duration',20),20,40)
    dx=random_range(next_math('treasure_flight_x',20),10,30)
    dz=random_range(next_math('treasure_flight_z',20),10,30)
    start=tuple(f32(v) for v in position)
    return dict(treasureId=treasure_id,start=start,
                end=(f32(start[0]+f32(dx)),start[1],f32(start[2]+f32(dz))),
                projectileType=1,height=duration*2,duration=duration,rotation=0,
                smokePosition=(start[0],f32(start[1]+10.),start[2]))
