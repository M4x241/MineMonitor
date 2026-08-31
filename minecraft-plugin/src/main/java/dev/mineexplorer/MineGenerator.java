package dev.mineexplorer;

import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;

import java.util.HashSet;
import java.util.Random;
import java.util.Set;

/**
 * Genera una red de galerías mineras determinista a partir de una seed:
 * túneles con ramificaciones, cámaras, obstrucciones (bloqueos) y zonas
 * de riesgo estructural etiquetadas (grietas, roca suelta, colapso).
 */
public class MineGenerator {

    private static final Material[] WALL_MATERIALS = {
            Material.STONE, Material.STONE, Material.STONE, Material.COBBLESTONE, Material.DEEPSLATE
    };
    private static final Material[] DEBRIS = {
            Material.ANDESITE, Material.DIORITE, Material.GRANITE, Material.COBBLED_DEEPSLATE
    };

    private final MineExplorerPlugin plugin;
    private final World world;
    private final String seed;
    private final int half;
    private final int startX, startY, startZ;
    private final Random rnd;

    private final Set<Long> carved = new HashSet<>();
    private int riskBlocks;
    private int obstructionBlocks;
    private Location droneSpawn;

    public MineGenerator(MineExplorerPlugin plugin, World world, String seed,
                         int half, int startX, int startY, int startZ) {
        this.plugin = plugin;
        this.world = world;
        this.seed = seed;
        this.half = half;
        this.startX = startX;
        this.startY = startY;
        this.startZ = startZ;
        this.rnd = new Random(seed.hashCode());
    }

    public void generateIfNeeded() {
        generateIfNeeded(false);
    }

    public void generateIfNeeded(boolean force) {
        String generated = plugin.getConfig().getString("generated-seed", null);
        boolean hasMarker = world.getBlockAt(new Location(world, startX, startY + 3, startZ)).getType() == Material.BEACON;
        if (!force && seed.equals(generated) && hasMarker) {
            plugin.getLogger().info("Mina ya generada con seed=" + seed);
            return;
        }
        generate();
        plugin.getConfig().set("generated-seed", seed);
        plugin.saveConfig();
    }

    private void generate() {
        long t0 = System.currentTimeMillis();
        carved.clear();
        riskBlocks = 0;
        obstructionBlocks = 0;

        // Pozo de entrada: desde la superficie hasta el interior.
        int surfaceY = startY;
        for (int y = surfaceY; y >= startY - 6; y--) {
            carveBlock(startX, y, startZ);
        }
        // Entrada ensanchada a ras de superficie.
        for (int dx = -1; dx <= 1; dx++) {
            for (int dz = -1; dz <= 1; dz++) {
                carveBlock(startX + dx, surfaceY, startZ + dz);
            }
        }
        // Marcador inmutable de generación.
        world.getBlockAt(startX, surfaceY + 1, startZ).setType(Material.BEACON, false);
        world.getBlockAt(startX, surfaceY + 2, startZ).setType(Material.STONE, false);
        droneSpawn = new Location(world, startX + 0.5, startY - 3.0, startZ + 0.5);

        // Red de galerías mediante caminata aleatoria con ramificación.
        int x = startX, y = startY - 3, z = startZ;
        int dirX = 1, dirZ = 0;
        int branchCount = 0;
        int steps = 0;
        int maxSteps = half * half / 2;

        while (steps < maxSteps) {
            carveGalleryBox(x, y, z);

            // Cámara ocasional.
            if (rnd.nextInt(45) == 0) {
                carveChamber(x, y, z);
            }
            // Obstrucción (bloqueo de galería).
            if (rnd.nextInt(18) == 0) {
                placeDebris(x, y, z);
            }
            // Zona de riesgo estructural.
            if (rnd.nextInt(30) == 0) {
                carveRiskZone(x, y, z);
            }
            // Ramificación.
            if (rnd.nextInt(24) == 0 && branchCount < 8) {
                branchCount++;
                spawnBranch(x, y, z);
            }

            // Cambio de dirección.
            if (rnd.nextInt(6) == 0) {
                int d = rnd.nextInt(4);
                dirX = switch (d) {
                    case 0 -> 1;
                    case 1 -> -1;
                    default -> 0;
                };
                dirZ = switch (d) {
                    case 2 -> 1;
                    case 3 -> -1;
                    default -> 0;
                };
                if (dirX == 0 && dirZ == 0) {
                    dirX = 1;
                }
            }
            x += dirX;
            z += dirZ;
            // Leve variación de altitud (desnivel de galería).
            if (rnd.nextInt(7) == 0) {
                y += rnd.nextBoolean() ? 1 : -1;
            }
            y = Math.max(16, Math.min(48, y));
            steps++;
        }

        plugin.getLogger().info(String.format(
                "Mina generada en %d ms | riesgo=%d | obstrucciones=%d | entrada=(%d,%d,%d)",
                System.currentTimeMillis() - t0, riskBlocks, obstructionBlocks, startX, startY, startZ));
    }

    /** Caminata recursiva limitada para una rama secundaria. */
    private void spawnBranch(int ox, int oy, int oz) {
        int x = ox, y = oy, z = oz;
        int dx = 0, dz = 1;
        if (rnd.nextBoolean()) {
            dx = 1;
            dz = 0;
            if (rnd.nextBoolean()) {
                dx = -1;
            }
        } else {
            dz = rnd.nextBoolean() ? 1 : -1;
        }
        int len = 6 + rnd.nextInt(10);
        for (int i = 0; i < len; i++) {
            carveGalleryBox(x, y, z);
            x += dx;
            z += dz;
            if (rnd.nextInt(5) == 0) {
                y += rnd.nextBoolean() ? 1 : -1;
            }
            y = Math.max(16, Math.min(48, y));
        }
    }

    /** Túnel de galería: 3x3 con techo y suelo desbastados. */
    private void carveGalleryBox(int x, int y, int z) {
        for (int dx = -1; dx <= 1; dx++) {
            for (int dy = -1; dy <= 1; dy++) {
                for (int dz = -1; dz <= 1; dz++) {
                    carveBlock(x + dx, y + dy, z + dz);
                }
            }
        }
        // Techo y suelo irregulares.
        if (rnd.nextInt(4) == 0) {
            carveBlock(x, y + 2, z);
        }
        if (rnd.nextInt(4) == 0) {
            carveBlock(x, y - 2, z);
        }
    }

    private void carveChamber(int x, int y, int z) {
        for (int dx = -3; dx <= 3; dx++) {
            for (int dy = -2; dy <= 2; dy++) {
                for (int dz = -3; dz <= 3; dz++) {
                    carveBlock(x + dx, y + dy, z + dz);
                }
            }
        }
    }

    private void carveBlock(int x, int y, int z) {
        if (x < -half || x > half || z < -half || z > half || y < 10 || y > 52) {
            return;
        }
        long key = ((long) x + half) * 1048576 + ((long) z + half) * 1024 + (long) y;
        if (!carved.add(key)) {
            return;
        }
        world.getBlockAt(x, y, z).setType(Material.CAVE_AIR, false);
    }

    /** Bloqueo parcial de la galería con escombros (label de riesgo 3). */
    private void placeDebris(int x, int y, int z) {
        Material mat = DEBRIS[rnd.nextInt(DEBRIS.length)];
        int n = 1 + rnd.nextInt(3);
        for (int i = 0; i < n; i++) {
            int dx = rnd.nextInt(3) - 1;
            int dy = rnd.nextInt(2);
            int dz = rnd.nextInt(3) - 1;
            setMineBlock(x + dx, y + dy, z + dz, mat);
            obstructionBlocks++;
        }
    }

    /**
     * Zona de riesgo: un bolsillo dañado con grietas (label 1) y
     * roca suelta en techo/suelo (label 2).
     */
    private void carveRiskZone(int x, int y, int z) {
        for (int dx = -2; dx <= 2; dx++) {
            for (int dy = -2; dy <= 2; dy++) {
                for (int dz = -2; dz <= 2; dz++) {
                    if (rnd.nextInt(3) != 0) {
                        continue;
                    }
                    int bx = x + dx, by = y + dy, bz = z + dz;
                    Material mat = switch (rnd.nextInt(8)) {
                        case 0, 1, 2 -> Material.CRACKED_STONE_BRICKS;
                        case 3, 4 -> Material.GRAVEL;
                        default -> Material.STONE;
                    };
                    if (mat == Material.STONE) {
                        carveBlock(bx, by, bz);
                    } else {
                        setMineBlock(bx, by, bz, mat);
                        riskBlocks++;
                    }
                }
            }
        }
    }

    private void setMineBlock(int x, int y, int z, Material mat) {
        if (x < -half || x > half || z < -half || z > half || y < 10 || y > 52) {
            return;
        }
        world.getBlockAt(x, y, z).setType(mat, false);
    }

    public Location getDroneSpawn() {
        return droneSpawn;
    }

    public int getRiskBlocks() {
        return riskBlocks;
    }

    public int getObstructionBlocks() {
        return obstructionBlocks;
    }

    public int getRegionHalf() {
        return half;
    }

    /** Mapeo material -> etiqueta de riesgo (verdad de terreno para la 3D-CNN). */
    public static int riskLabel(Material mat) {
        return switch (mat) {
            case CRACKED_STONE_BRICKS -> 1;
            case GRAVEL, SAND, DIRT -> 2;
            case ANDESITE, DIORITE, GRANITE, COBBLED_DEEPSLATE, TUFF -> 3;
            case LAVA, MAGMA_BLOCK -> 4;
            default -> 0;
        };
    }
}
