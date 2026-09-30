package dev.mineexplorer;

/**
 * Especificación de un nivel de mina para la generación por niveles.
 * Cada nivel vive en su propia caja (bounds) y tiene su propia complejidad:
 * más pasos, ramas, cámaras, debris y zonas de riesgo según el nivel.
 */
public final class MineLevelSpec {

    public final String name;
    public final int half;

    public final int centerX, centerY, centerZ;

    public final int maxSteps;
    public final int branchEvery;
    public final int maxBranches;
    public final int branchLenMin;
    public final int branchLenMax;
    public final int chamberEvery;
    public final int debrisEvery;
    public final int riskEvery;

    public final int minY, maxY;

    public MineLevelSpec(String name, int half,
                         int centerX, int centerY, int centerZ,
                         int maxSteps, int branchEvery, int maxBranches,
                         int branchLenMin, int branchLenMax,
                         int chamberEvery, int debrisEvery, int riskEvery,
                         int minY, int maxY) {
        this.name = name;
        this.half = half;
        this.centerX = centerX;
        this.centerY = centerY;
        this.centerZ = centerZ;
        this.maxSteps = maxSteps;
        this.branchEvery = branchEvery;
        this.maxBranches = maxBranches;
        this.branchLenMin = branchLenMin;
        this.branchLenMax = branchLenMax;
        this.chamberEvery = chamberEvery;
        this.debrisEvery = debrisEvery;
        this.riskEvery = riskEvery;
        this.minY = minY;
        this.maxY = maxY;
    }

    public int minX() {
        return centerX - half;
    }

    public int maxX() {
        return centerX + half;
    }

    public int minZ() {
        return centerZ - half;
    }

    public int maxZ() {
        return centerZ + half;
    }
}