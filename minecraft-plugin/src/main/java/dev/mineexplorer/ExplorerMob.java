package dev.mineexplorer;

import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.World;
import org.bukkit.entity.EntityType;
import org.bukkit.entity.Vex;
import org.bukkit.util.Vector;
import org.bukkit.scheduler.BukkitRunnable;

/**
 * El "dron" explorador: una entidad Vex (criatura voladora) con AI desactivada.
 * Python decide el siguiente punto (NBV) y este controlador lo mueve con
 * interpolación suave (velocidad en bloques por segundo).
 */
public class ExplorerMob {

    private final MineExplorerPlugin plugin;
    private final World world;
    private Vex vex;

    private Location target;
    private double speed;

    private BukkitRunnable mover;
    private final Location defaultSpawn;
    private boolean invalidLogged = false;
    private long lastRespawnAttempt = 0;

    public ExplorerMob(MineExplorerPlugin plugin, World world, Location defaultSpawn) {
        this.plugin = plugin;
        this.world = world;
        this.defaultSpawn = defaultSpawn;
    }

    public void spawn() {
        remove();
        vex = (Vex) world.spawnEntity(defaultSpawn, EntityType.VEX);
        vex.setAI(false);
        vex.setGravity(false);
        vex.setInvulnerable(true);
        vex.setSilent(true);
        vex.setPersistent(true);
        vex.setCollidable(false);
        vex.setInvisible(false);
        vex.setGlowing(true);
        startMover();
        plugin.getLogger().info("Mob explorador desplegado en " + shortLoc(vex.getLocation())
                + " id=" + vex.getEntityId()
                + " chunkCargado=" + world.isChunkLoaded(vex.getLocation().getChunk())
                + " valido=" + vex.isValid());
    }

    private void startMover() {
        if (mover != null) {
            mover.cancel();
        }
        mover = new BukkitRunnable() {
            @Override
            public void run() {
                tick();
            }
        };
        mover.runTaskTimer(plugin, 0L, 1L);
    }

    private void tick() {
        if (vex == null || !vex.isValid()) {
            long now = System.currentTimeMillis();
            if (!invalidLogged) {
                plugin.getLogger().warning("Mob explorador invalido/null; target="
                        + (target != null ? target.toVector() : "null")
                        + " | entidades en mundo=" + world.getEntities().size()
                        + " | chunk=" + defaultSpawn.getChunk().getX() + "," + defaultSpawn.getChunk().getZ()
                        + " cargado=" + world.isChunkLoaded(defaultSpawn.getChunk()));
                invalidLogged = true;
            }
            if (now - lastRespawnAttempt > 3000) {
                lastRespawnAttempt = now;
                invalidLogged = false;
                plugin.getLogger().info("Re-desplegando mob explorador...");
                spawn();
            }
            return;
        }        if (target == null) {
            return;
        }
        Location cur = vex.getLocation();
        double dist = cur.distance(target);
        double step = speed / 20.0;
        if (dist <= step || dist == 0) {
            vex.teleport(target);
            target = null;
            return;
        }
        Vector dir = target.toVector().subtract(cur.toVector()).normalize().multiply(step);
        Location next = cur.clone().add(dir);
        vex.teleport(next);
        float yaw = (float) Math.toDegrees(Math.atan2(-dir.getX(), dir.getZ()));
        vex.setRotation(yaw, 0f);
    }

    /** Ordena moverse hacia una posición a cierta velocidad (bloques/seg). */
    public void moveTo(Location target, double speed) {
        this.target = target.clone();
        this.speed = Math.max(0.1, Math.min(speed, 20.0));
    }

    public void teleport(Location loc) {
        target = null;
        if (vex != null && vex.isValid()) {
            vex.teleport(loc);
        }
    }

    public Location getLocation() {
        if (vex != null && vex.isValid()) {
            return vex.getLocation();
        }
        return defaultSpawn.clone();
    }

    public Location getOrigin() {
        return defaultSpawn.clone();
    }

    public org.bukkit.entity.Entity getTargetEntity() {
        return vex;
    }

    public boolean isAtTarget() {
        return target == null;
    }

    public boolean isValid() {
        return vex != null && vex.isValid();
    }

    public String getTypeName() {
        return vex != null && vex.isValid() ? vex.getType().name() : "NONE";
    }

    /** ¿Es esta la entidad que controlamos? */
    public boolean tracks(org.bukkit.entity.Entity other) {
        return vex != null && vex.getEntityId() == other.getEntityId();
    }

    public MineExplorerPlugin getPlugin() {
        return plugin;
    }

    public void remove() {
        if (mover != null) {
            mover.cancel();
            mover = null;
        }
        if (vex != null) {
            vex.remove();
            vex = null;
        }
    }

    private String shortLoc(Location l) {
        return String.format("(%.1f, %.1f, %.1f)", l.getX(), l.getY(), l.getZ());
    }
}
