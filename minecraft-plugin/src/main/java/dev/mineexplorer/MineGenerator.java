package dev.mineexplorer;

import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Random;
import java.util.Set;

/**
 * Generador de la mina por niveles: crea varias redes de galerías ("niveles")
 * lados a lado en el eje X dentro del mismo mundo, cada una con su propia
 * complejidad y su propia caja (bounds). La disposición es determinista para
 * una seed dada, así que cada mina de la BD (con su seed) produce un mapa
 * distinto pero reproducible.
 */
public class MineGenerator {

    static final Material[] WALL_MATERIALS = {
            Material.STONE, Material.STONE, Material.STONE,
            Material.COBBLESTONE, Material.DEEPSLATE,
    };
    static final Material[] DEBRIS = {
            Material.ANDESITE, Material.DIORITE, Material.GRANITE, Material.COBBLED_DEEPSLATE,
    };

    private final MineExplorerPlugin plugin;
    private final World world;
    private final String seed;
    private final int half;
    private final List<MineLevelSpec> levels = new ArrayList<>();

    private final Set<Long> carved = new HashSet<>();
    private MineLevelSpec cur;
    private int riskBlocks;
    private int obstructionBlocks;
    private Location droneSpawn;

    public MineGenerator(MineExplorerPlugin plugin, World world, String seed,
                         int half, int startX, int startY, int startZ) {
        this.plugin = plugin;
        this.world = world;
        this.seed = seed;
        this.half = half;
        // El spawn del dron queda a startY-3 (bajo la superficie); los niveles se
        // tallan a esa altura, cada uno en su caja lateral a lo largo de X.
        buildLevels(startX, startY - 3, startZ);
        initDroneSpawn();
    }

    /** Fija el spawn del dron al centro del primer nivel (inicio de la mina). */
    private void initDroneSpawn() {
        MineLevelSpec first = levels.get(0);
        droneSpawn = new Location(world, first.centerX + 0.5, first.centerY, first.centerZ + 0.5);
    }

    private int startSurfaceY() {
        return levels.get(0).centerY + 6;
    }

    /** Construye los 4 niveles lado a lado en X, deterministas para esta seed. */
    private void buildLevels(int startX, int startY, int startZ) {
        int gap = 2 * half + 16;

        // Nivel 1 "Sencilla": poco recorrido, sin ramas ni estructuras.
        addLevel("Sencilla", startX, startZ,
                startY, half * half / 8, 1_000_000, 0, 6, 10, 1_000_000, 1_000_000, 1_000_000);
        // Nivel 2 "Compleja": más pasos y alguna rama secundaria.
        addLevel("Compleja", startX + gap, startZ,
                startY, half * half / 6, 26, 3, 6, 14, 70, 32, 60);
        // Nivel 3 "Estructura de mina": cámaras, debris y riesgo moderado.
        addLevel("Estructura", startX + 2 * gap, startZ,
                startY, half * half / 4, 22, 5, 7, 16, 40, 22, 38);
        // Nivel 4 "Profundo": red densa, muchas ramas y estructuras.
        addLevel("Profundo", startX + 3 * gap, startZ,
                startY, half * half / 2, 16, 8, 8, 18, 24, 14, 26);
    }

    private void addLevel(String name,
                          int cx, int cz, int cy,
                          int maxSteps, int branchEvery, int maxBranches,
                          int branchLenMin, int branchLenMax,
                          int chamberEvery, int debrisEvery, int riskEvery) {
        levels.add(new MineLevelSpec(name, half, cx, cy, cz,
                maxSteps, branchEvery, maxBranches, branchLenMin, branchLenMax,
                chamberEvery, debrisEvery, riskEvery, Math.max(10, cy - 12), Math.min(52, cy + 12)));
    }

    public void generateIfNeeded() {
        generateIfNeeded(false);
    }

    public void generateIfNeeded(boolean force) {
        String generated = plugin.getConfig().getString("generated-seed", null);
        boolean hasMarker = world.getBlockAt(new Location(world, levels.get(0).centerX,
                startSurfaceY(), levels.get(0).centerZ)).getType() == Material.BEACON;
        if (!force && seed.equals(generated) && hasMarker) {
            plugin.getLogger().info("Mina ya generada con seed=" + seed);
            return;
        }
        resetRegion();
        generate();
        plugin.getConfig().set("generated-seed", seed);
        plugin.saveConfig();
    }

    /** Limpia toda la huella de la mina devolviéndola a piedra (previo a regenerar). */
    public void resetRegion() {
        int minX = Integer.MAX_VALUE, maxX = Integer.MIN_VALUE;
        int minZ = Integer.MAX_VALUE, maxZ = Integer.MIN_VALUE;
        for (MineLevelSpec l : levels) {
            minX = Math.min(minX, l.minX());
            maxX = Math.max(maxX, l.maxX());
            minZ = Math.min(minZ, l.minZ());
            maxZ = Math.max(maxZ, l.maxZ());
        }
        int minY = 10, maxY = startSurfaceY() + 1;
        int restored = 0;
        for (int x = minX; x <= maxX; x++) {
            for (int z = minZ; z <= maxZ; z++) {
                for (int y = minY; y <= maxY; y++) {
                    // Solo reescribe lo que ya no es piedra (galerías/escombros de
                    // una generación previa); la región base es piedra sólida.
                    Material m = world.getBlockAt(x, y, z).getType();
                    if (m != Material.STONE) {
                        world.getBlockAt(x, y, z).setType(Material.STONE, false);
                        restored++;
                    }
                }
            }
        }
        plugin.getLogger().info("Región de la mina restablecida a piedra (restaurados=" + restored
                + ", x " + minX + ".." + maxX + ", z " + minZ + ".." + maxZ + ")");
    }

    public void generate() {
        long t0 = System.currentTimeMillis();
        riskBlocks = 0;
        obstructionBlocks = 0;

        // Solo el primer nivel tiene pozo de entrada desde la superficie.
        MineLevelSpec first = levels.get(0);
        cur = first;
        int surfaceY = startSurfaceY();
        for (int y = surfaceY; y >= first.centerY - 2; y--) {
            carveBlock(first.centerX, y, first.centerZ);
        }
        for (int dx = -1; dx <= 1; dx++) {
            for (int dz = -1; dz <= 1; dz++) {
                carveBlock(first.centerX + dx, surfaceY, first.centerZ + dz);
            }
        }
        world.getBlockAt(first.centerX, surfaceY, first.centerZ).setType(Material.BEACON, false);
        world.getBlockAt(first.centerX, surfaceY + 1, first.centerZ).setType(Material.STONE, false);
        initDroneSpawn();

        int totalRisk = 0;
        int totalObstruction = 0;
        for (MineLevelSpec level : levels) {
            carved.clear();
            generateLevel(level);
            totalRisk += riskBlocks;
            totalObstruction += obstructionBlocks;
        }
        riskBlocks = totalRisk;
        obstructionBlocks = totalObstruction;

        plugin.getLogger().info(String.format(
                "Mina generada en %d ms | niveles=%d | riesgo=%d | obstrucciones=%d | spawn=%s",
                System.currentTimeMillis() - t0, levels.size(),
                totalRisk, totalObstruction, shortLoc(droneSpawn)));
    }

    private void generateLevel(MineLevelSpec level) {
        cur = level;
        riskBlocks = 0;
        obstructionBlocks = 0;

        Random rnd = new Random((seed + "#" + level.name).hashCode());
        int x = level.centerX, y = level.centerY, z = level.centerZ;
        int dirX = 1, dirZ = 0;
        int branchCount = 0;
        int steps = 0;

        while (steps < level.maxSteps) {
            carveGalleryBox(x, y, z, rnd);

            if (rnd.nextInt(level.chamberEvery) == 0) {
                carveChamber(x, y, z, rnd);
            }
            if (rnd.nextInt(level.debrisEvery) == 0) {
                placeDebris(x, y, z, rnd);
            }
            if (rnd.nextInt(level.riskEvery) == 0) {
                carveRiskZone(x, y, z, rnd);
            }
            if (rnd.nextInt(level.branchEvery) == 0 && branchCount < level.maxBranches) {
                branchCount++;
                spawnBranch(x, y, z, rnd, level);
            }

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
            if (rnd.nextInt(7) == 0) {
                y += rnd.nextBoolean() ? 1 : -1;
            }
            y = Math.max(level.minY + 2, Math.min(level.maxY - 2, y));
            steps++;
        }
    }

    private void spawnBranch(int ox, int oy, int oz, Random rnd, MineLevelSpec level) {
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
        int len = level.branchLenMin + rnd.nextInt(level.branchLenMax - level.branchLenMin + 1);
        for (int i = 0; i < len; i++) {
            carveGalleryBox(x, y, z, rnd);
            x += dx;
            z += dz;
            if (rnd.nextInt(5) == 0) {
                y += rnd.nextBoolean() ? 1 : -1;
            }
            y = Math.max(cur.minY + 2, Math.min(cur.maxY - 2, y));
        }
    }

    private void carveGalleryBox(int x, int y, int z, Random rnd) {
        for (int dx = -1; dx <= 1; dx++) {
            for (int dy = -1; dy <= 1; dy++) {
                for (int dz = -1; dz <= 1; dz++) {
                    carveBlock(x + dx, y + dy, z + dz);
                }
            }
        }
        if (rnd.nextInt(4) == 0) {
            carveBlock(x, y + 2, z);
        }
        if (rnd.nextInt(4) == 0) {
            carveBlock(x, y - 2, z);
        }
    }

    private void carveChamber(int x, int y, int z, Random rnd) {
        for (int dx = -3; dx <= 3; dx++) {
            for (int dy = -2; dy <= 2; dy++) {
                for (int dz = -3; dz <= 3; dz++) {
                    carveBlock(x + dx, y + dy, z + dz);
                }
            }
        }
    }

    private void carveBlock(int x, int y, int z) {
        MineLevelSpec l = cur;
        if (x < l.minX() || x > l.maxX() || z < l.minZ() || z > l.maxZ() || y < 10 || y > 52 || y < l.minY || y > l.maxY) {
            return;
        }
        long key = ((long) x + 4096) * 16_777_216L + ((long) z + 4096) * 4096L + ((long) y + 2048);
        if (!carved.add(key)) {
            return;
        }
        world.getBlockAt(x, y, z).setType(Material.CAVE_AIR, false);
    }

    private void placeDebris(int x, int y, int z, Random rnd) {
        Material mat = DEBRIS[rnd.nextInt(DEBRIS.length)];
        int n = 1 + rnd.nextInt(3);
        for (int i = 0; i < n; i++) {
            int dx = rnd.nextInt(3) - 1;
            int dy = rnd.nextInt(2);
            int dz = rnd.nextInt(3) - 1;
            if (setMineBlock(x + dx, y + dy, z + dz, mat)) {
                obstructionBlocks++;
            }
        }
    }

    private void carveRiskZone(int x, int y, int z, Random rnd) {
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
                    } else if (setMineBlock(bx, by, bz, mat)) {
                        riskBlocks++;
                    }
                }
            }
        }
    }

    private boolean setMineBlock(int x, int y, int z, Material mat) {
        MineLevelSpec l = cur;
        if (x < l.minX() || x > l.maxX() || z < l.minZ() || z > l.maxZ() || y < 10 || y > 52 || y < l.minY || y > l.maxY) {
            return false;
        }
        world.getBlockAt(x, y, z).setType(mat, false);
        return true;
    }

    public Location getDroneSpawn() {
        return droneSpawn;
    }

    public List<MineLevelSpec> getLevels() {
        return levels;
    }

    public List<Location> getLevelSpawns() {
        List<Location> out = new ArrayList<>();
        for (MineLevelSpec l : levels) {
            out.add(new Location(world, l.centerX + 0.5, l.centerY, l.centerZ + 0.5));
        }
        return out;
    }

    public MineLevelSpec getLevel(int idx) {
        return levels.get(idx);
    }

    public int levelCount() {
        return levels.size();
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

    public MineExplorerPlugin plugin() {
        return plugin;
    }

    private String shortLoc(Location l) {
        return String.format("(%.1f, %.1f, %.1f)", l.getX(), l.getY(), l.getZ());
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