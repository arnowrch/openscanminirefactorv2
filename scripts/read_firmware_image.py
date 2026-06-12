"""
Read OpenScan firmware files directly from ext4 disk image.
Extracts all settings, GPIO configs, and firmware code.
"""

import struct, os, sys, json
from pathlib import Path

IMG_PATH = r"C:\Users\arnow\open scan mini refactor\original_firmeware\2024-09-09 OpenScan\media\t\764EE5484EE501AB\orig.img"
PARTITION_OFFSET = 272629760  # LBA 532480 * 512


class Ext4Reader:
    """Minimal ext4 filesystem reader for extracting specific files."""

    def __init__(self, img_path, part_offset):
        self.img   = open(img_path, 'rb')
        self.poff  = part_offset
        self._read_superblock()

    # ------------------------------------------------------------------ helpers
    def _abs(self, fs_byte_offset):
        return self.poff + fs_byte_offset

    def _seek_read(self, fs_offset, n):
        self.img.seek(self._abs(fs_offset))
        return self.img.read(n)

    # ---------------------------------------------------------------- superblock
    def _read_superblock(self):
        sb = self._seek_read(1024, 1024)
        self.block_size       = 1024 << struct.unpack_from('<I', sb, 24)[0]
        self.blocks_per_group = struct.unpack_from('<I', sb, 32)[0]
        self.inodes_per_group = struct.unpack_from('<I', sb, 40)[0]
        self.inode_size       = struct.unpack_from('<H', sb, 88)[0]
        feat_incompat         = struct.unpack_from('<I', sb, 96)[0]
        self.has_64bit        = bool(feat_incompat & 0x80)
        self.gd_size          = 64 if self.has_64bit else 32
        magic = struct.unpack_from('<H', sb, 56)[0]
        assert magic == 0xEF53, f"Bad ext4 magic: {magic:#x}"
        bs = self.block_size
        # GDT starts at the block right after the superblock block
        self.gdt_start_block  = 0 if bs > 1024 else 2   # block 0 has SB when bs=1024; bs=4096 → SB in block 0, GDT in block 1
        self.gdt_start_block  = 1  # always block 1 for 4 KiB blocks
        print(f"[ext4] block_size={bs}, inode_size={self.inode_size}, "
              f"inodes/group={self.inodes_per_group}, gd_size={self.gd_size}, 64bit={self.has_64bit}")

    # ---------------------------------------------------------------- block I/O
    def _blk_offset(self, blk_no):
        return blk_no * self.block_size

    def _read_block(self, blk_no):
        return self._seek_read(self._blk_offset(blk_no), self.block_size)

    # ---------------------------------------------------- group descriptor table
    def _inode_table_blk(self, group):
        gdt_byte = self._blk_offset(self.gdt_start_block) + group * self.gd_size
        gd = self._seek_read(gdt_byte, self.gd_size)
        lo = struct.unpack_from('<I', gd, 8)[0]
        hi = struct.unpack_from('<I', gd, 40)[0] if self.gd_size >= 44 else 0
        return lo | (hi << 32)

    # ------------------------------------------------------------------- inodes
    def _read_inode(self, ino):
        g   = (ino - 1) // self.inodes_per_group
        idx = (ino - 1) %  self.inodes_per_group
        tbl = self._inode_table_blk(g)
        off = self._blk_offset(tbl) + idx * self.inode_size
        data = self._seek_read(off, self.inode_size)
        if len(data) < self.inode_size:
            raise IOError(f"Short inode read for ino={ino}: got {len(data)} bytes")
        return data

    # ------------------------------------------------------------------ extents
    def _file_blocks_and_size(self, inode):
        flags    = struct.unpack_from('<I', inode, 32)[0]
        size_lo  = struct.unpack_from('<I', inode,  4)[0]
        size_hi  = struct.unpack_from('<I', inode, 108)[0]
        file_sz  = size_lo | (size_hi << 32)
        raw      = inode[40:100]                  # i_block[15]

        blks = []
        if flags & 0x80000:                        # extent tree
            magic  = struct.unpack_from('<H', raw, 0)[0]
            if magic == 0xF30A:
                n_ent  = struct.unpack_from('<H', raw, 2)[0]
                depth  = struct.unpack_from('<H', raw, 6)[0]
                if depth == 0:                     # leaf: direct extents
                    for i in range(n_ent):
                        base = 12 + i * 12
                        ee_block = struct.unpack_from('<I', raw, base)[0]     # logical start
                        ee_len   = struct.unpack_from('<H', raw, base+4)[0]   # block count
                        ee_lo    = struct.unpack_from('<I', raw, base+8)[0]
                        ee_hi    = struct.unpack_from('<H', raw, base+6)[0]
                        phys     = ee_lo | (ee_hi << 32)
                        for j in range(ee_len):
                            blks.append(phys + j)
                else:                              # interior node – follow first level only
                    for i in range(n_ent):
                        base = 12 + i * 12
                        idx_lo = struct.unpack_from('<I', raw, base+4)[0]
                        idx_hi = struct.unpack_from('<I', raw, base+8)[0] if self.has_64bit else 0
                        node_blk = idx_lo | (idx_hi << 32)
                        node_data = self._read_block(node_blk)
                        nm = struct.unpack_from('<H', node_data, 0)[0]
                        nd = struct.unpack_from('<H', node_data, 6)[0]
                        ne = struct.unpack_from('<H', node_data, 2)[0]
                        if nm == 0xF30A and nd == 0:
                            for j in range(ne):
                                b2 = 12 + j * 12
                                l  = struct.unpack_from('<H', node_data, b2+4)[0]
                                lo = struct.unpack_from('<I', node_data, b2+8)[0]
                                hi = struct.unpack_from('<H', node_data, b2+6)[0]
                                p2 = lo | (hi << 32)
                                for k in range(l):
                                    blks.append(p2 + k)
        else:                                      # direct/indirect blocks
            for i in range(12):
                b = struct.unpack_from('<I', raw, i*4)[0]
                if b: blks.append(b)
        return blks, file_sz

    # ----------------------------------------------------------------- read file
    def read_inode(self, ino):
        inode = self._read_inode(ino)
        blks, sz = self._file_blocks_and_size(inode)
        buf = b''.join(self._read_block(b) for b in blks)
        return buf[:sz]

    # --------------------------------------------------------------- list dir
    def list_dir(self, ino):
        inode = self._read_inode(ino)
        blks, _ = self._file_blocks_and_size(inode)
        out = {}
        for b in blks:
            data = self._read_block(b)
            off  = 0
            while off < len(data):
                if off + 8 > len(data): break
                e_ino   = struct.unpack_from('<I', data, off)[0]
                rec_len = struct.unpack_from('<H', data, off+4)[0]
                n_len   = data[off+6]
                if rec_len == 0: break
                if e_ino and n_len:
                    name = data[off+8: off+8+n_len].decode('utf-8', errors='replace')
                    out[name] = e_ino
                off += rec_len
        return out

    # ------------------------------------------------------------- path resolve
    def find(self, path):
        ino = 2
        for part in [p for p in path.strip('/').split('/') if p]:
            entries = self.list_dir(ino)
            if part not in entries:
                return None
            ino = entries[part]
        return ino

    def read(self, path):
        ino = self.find(path)
        return self.read_inode(ino) if ino else None

    def listdir(self, path):
        ino = self.find(path)
        return self.list_dir(ino) if ino else None

    def close(self):
        self.img.close()


# ===========================================================================
def main():
    print(f"Image: {IMG_PATH}")
    print(f"Offset: {PARTITION_OFFSET}\n")

    r = Ext4Reader(IMG_PATH, PARTITION_OFFSET)
    collected = {}

    # ── 1. All Settings files ──────────────────────────────────────────────
    print("\n" + "="*60)
    print("1. OPENSCAN SETTINGS  (/home/pi/OpenScan/settings/)")
    print("="*60)
    entries = r.listdir("/home/pi/OpenScan/settings")
    if entries:
        for name in sorted(entries):
            if name in ('.', '..'): continue
            raw = r.read_inode(entries[name])
            val = raw.decode('utf-8', errors='replace').strip()
            print(f"  {name:<35} = {val}")
            collected[name] = val
    else:
        print("  *** NICHT GEFUNDEN ***")

    # ── 2. OpenScan.py ────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("2. OpenScan.py")
    print("="*60)
    for p in ["/home/pi/OpenScan/OpenScan.py", "/home/pi/OpenScan/scripts/OpenScan.py"]:
        raw = r.read(p)
        if raw:
            txt = raw.decode('utf-8', errors='replace')
            print(txt[:4000])
            collected['OpenScan.py'] = txt
            break

    # ── 3. fla.py (Flask camera server) ───────────────────────────────────
    print("\n" + "="*60)
    print("3. fla.py")
    print("="*60)
    for p in ["/home/pi/OpenScan/fla.py", "/home/pi/fla.py"]:
        raw = r.read(p)
        if raw:
            txt = raw.decode('utf-8', errors='replace')
            print(txt[:3000])
            collected['fla.py'] = txt
            break

    # ── 4. config.txt ────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("4. /boot/config.txt")
    print("="*60)
    for p in ["/boot/firmware/config.txt", "/boot/config.txt"]:
        raw = r.read(p)
        if raw:
            print(raw.decode('utf-8', errors='replace'))
            collected['config.txt'] = raw.decode('utf-8', errors='replace')
            break

    # ── 5. Node-RED flows ────────────────────────────────────────────────
    print("\n" + "="*60)
    print("5. Node-RED flows.json (GPIO nodes)")
    print("="*60)
    for p in ["/home/pi/.node-red/flows.json", "/root/.node-red/flows.json"]:
        raw = r.read(p)
        if raw:
            try:
                flows = json.loads(raw.decode('utf-8'))
                print(f"  Total nodes: {len(flows)}")
                # Find pin / GPIO references
                for node in flows:
                    if not isinstance(node, dict): continue
                    n_type = node.get('type','')
                    n_name = node.get('name','')
                    n_pin  = node.get('pin','')
                    n_func = node.get('func','')
                    if n_pin or 'gpio' in n_type.lower():
                        print(f"  [{n_type}] {n_name} pin={n_pin}")
                    if 'pin_' in n_func or 'GPIO' in n_func:
                        # print first 200 chars of relevant func nodes
                        snippet = n_func[:300].replace('\n',' ')
                        print(f"  [func:{n_name}] {snippet}")
                collected['flows_node_count'] = len(flows)
            except Exception as e:
                print(f"  JSON error: {e}")
            break

    # ── 6. Camera detection ───────────────────────────────────────────────
    print("\n" + "="*60)
    print("6. Kamera-Konfiguration")
    print("="*60)
    for p in [
        "/home/pi/OpenScan/settings/camera",
        "/home/pi/OpenScan/settings/model",
        "/home/pi/OpenScan/settings/cam_resx",
        "/home/pi/OpenScan/settings/cam_resy",
        "/etc/udev/rules.d/99-arducam.rules",
        "/etc/udev/rules.d/99-camera.rules",
    ]:
        raw = r.read(p)
        if raw:
            print(f"  {p}: {raw.decode('utf-8',errors='replace').strip()}")

    r.close()

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("ZUSAMMENFASSUNG: GPIO PINS")
    print("="*60)
    for k,v in sorted(collected.items()):
        if 'pin' in k.lower():
            print(f"  {k:<40} = {v}")

    out = r"C:\Users\arnow\open scan mini refactor\configs\firmware_audit.json"
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(collected, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Audit gespeichert: {out}")


if __name__ == '__main__':
    main()
