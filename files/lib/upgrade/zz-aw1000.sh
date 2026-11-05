#!/bin/sh
# Arcadyan AW1000: validate the exact NAND TAR layout and finish upgrades with
# reboot -f first, without entering the generic unmount-all path.

AW1000_SYSUPGRADE_BOARD='arcadyan_aw1000'
AW1000_SYSUPGRADE_DIR="sysupgrade-${AW1000_SYSUPGRADE_BOARD}"
AW1000_SYSRQ_TRIGGER="${AW1000_SYSRQ_TRIGGER:-/proc/sysrq-trigger}"
AW1000_UPGRADE_MARK_MOUNT="${AW1000_UPGRADE_MARK_MOUNT:-/tmp/aw1000-upgrade-marker}"

aw1000_upgrade_abort() {
	printf 'AW1000 sysupgrade failed: %s\n' "$*" >&2
	sync
	exit 1
}

aw1000_tar_length() {
	tar xf "$1" "$2" -O 2>/dev/null | wc -c | awk '{ print $1 }'
}

aw1000_validate_sysupgrade() {
	local image="$1"
	local directory="$AW1000_SYSUPGRADE_DIR"
	local control members expected kernel_length rootfs_length

	[ "$(board_name)" = 'arcadyan,aw1000' ] || {
		echo 'Image is only valid for Arcadyan AW1000.' >&2
		return 1
	}

	control="$(tar xf "$image" "$directory/CONTROL" -O 2>/dev/null)" || return 1
	[ "$control" = "BOARD=$AW1000_SYSUPGRADE_BOARD" ] || {
		echo 'Invalid AW1000 sysupgrade CONTROL.' >&2
		return 1
	}

	members="$(tar tf "$image" 2>/dev/null)" || return 1
	expected="$(printf '%s\n' "$directory/" "$directory/CONTROL" "$directory/kernel" "$directory/root")"
	[ "$members" = "$expected" ] || {
		echo 'Invalid AW1000 sysupgrade member list.' >&2
		return 1
	}

	kernel_length="$(aw1000_tar_length "$image" "$directory/kernel")"
	rootfs_length="$(aw1000_tar_length "$image" "$directory/root")"
	case "$kernel_length" in
		''|*[!0-9]*|0)
			echo 'Empty or invalid AW1000 sysupgrade kernel.' >&2
			return 1
			;;
	esac
	case "$rootfs_length" in
		''|*[!0-9]*|0)
			echo 'Empty or invalid AW1000 sysupgrade root.' >&2
			return 1
			;;
	esac

	[ "$(identify_tar "$image" "$directory/kernel")" = fit ] || {
		echo 'AW1000 sysupgrade kernel is not FIT.' >&2
		return 1
	}
	[ "$(identify_tar "$image" "$directory/root")" = squashfs ] || {
		echo 'AW1000 sysupgrade root is not SquashFS.' >&2
		return 1
	}
	return 0
}

platform_check_image() {
	aw1000_validate_sysupgrade "$1"
}

aw1000_write_upgrade_marker() {
	local kernel_length="$1"
	local rootfs_length="$2"
	local ubidev data_ubivol mounted=0

	ubidev="$(nand_find_ubi "$CI_UBIPART")"
	[ -n "$ubidev" ] || return 1
	data_ubivol="$(nand_find_volume "$ubidev" rootfs_data)"
	[ -n "$data_ubivol" ] || return 1

	rmdir "$AW1000_UPGRADE_MARK_MOUNT" 2>/dev/null || true
	[ ! -e "$AW1000_UPGRADE_MARK_MOUNT" ] || return 1
	mkdir -p "$AW1000_UPGRADE_MARK_MOUNT" || return 1
	if mount -t ubifs "/dev/$data_ubivol" "$AW1000_UPGRADE_MARK_MOUNT"; then
		mounted=1
		{
			printf 'status=payload-written\n'
			printf 'board=arcadyan,aw1000\n'
			printf 'kernel_bytes=%s\n' "$kernel_length"
			printf 'rootfs_bytes=%s\n' "$rootfs_length"
		} > "$AW1000_UPGRADE_MARK_MOUNT/aw1000-sysupgrade.state" || mounted=0
		sync
		umount "$AW1000_UPGRADE_MARK_MOUNT" || mounted=0
	fi
	rmdir "$AW1000_UPGRADE_MARK_MOUNT" 2>/dev/null || true
	[ "$mounted" -eq 1 ]
}

aw1000_finish_upgrade() {
	local kernel_length="$1"
	local rootfs_length="$2"
	local conf_tar='/tmp/sysupgrade.tgz'

	sync
	if [ -f "$conf_tar" ]; then
		nand_restore_config "$conf_tar" || aw1000_upgrade_abort 'configuration restore failed'
	fi

	if ! aw1000_write_upgrade_marker "$kernel_length" "$rootfs_length"; then
		echo 'AW1000 sysupgrade warning: persistent completion marker unavailable.' >&2
	fi
	sync
	sleep 2
	sync
	echo 'AW1000 sysupgrade payload written successfully.'
	echo 'AW1000 sysupgrade: requesting reboot -f.'
	reboot -f
	sleep 5

	echo 'AW1000 sysupgrade: reboot -f returned; trying SysRq.' >&2
	if [ -w "$AW1000_SYSRQ_TRIGGER" ]; then
		printf 'b\n' > "$AW1000_SYSRQ_TRIGGER" 2>/dev/null
	fi

	# If SysRq unexpectedly returns, end the stage2 child. PID 1 upgraded then
	# attempts its own reboot. A blocked reboot syscall cannot reach this
	# fallback; no automatic watchdog recovery is guaranteed here.
	echo 'AW1000 sysupgrade: reset returned; handing reset to upgraded.' >&2
	exit 0
}

aw1000_nand_upgrade_tar() {
	local tar_file="$1"
	local directory="$AW1000_SYSUPGRADE_DIR"
	local kernel_length rootfs_length ubidev kern_ubivol root_ubivol

	aw1000_validate_sysupgrade "$tar_file" || aw1000_upgrade_abort 'image validation failed in stage2'
	[ -z "$(find_mtd_index kernel)" ] || aw1000_upgrade_abort 'unexpected standalone kernel MTD'
	CI_UBIPART='rootfs'
	CI_KERNPART='kernel'
	CI_ROOTPART='rootfs'

	kernel_length="$(aw1000_tar_length "$tar_file" "$directory/kernel")"
	rootfs_length="$(aw1000_tar_length "$tar_file" "$directory/root")"
	nand_upgrade_prepare_ubi "$rootfs_length" squashfs 1 0 || \
		aw1000_upgrade_abort 'UBI volume preparation failed'

	ubidev="$(nand_find_ubi "$CI_UBIPART")"
	[ -n "$ubidev" ] || aw1000_upgrade_abort 'rootfs UBI device not found after preparation'
	kern_ubivol="$(nand_find_volume "$ubidev" "$CI_KERNPART")"
	root_ubivol="$(nand_find_volume "$ubidev" "$CI_ROOTPART")"
	[ -n "$kern_ubivol" ] || aw1000_upgrade_abort 'kernel UBI volume not found'
	[ -n "$root_ubivol" ] || aw1000_upgrade_abort 'rootfs UBI volume not found'

	if ! tar xf "$tar_file" "$directory/kernel" -O | \
		ubiupdatevol "/dev/$kern_ubivol" -s "$kernel_length" -; then
		aw1000_upgrade_abort 'kernel UBI write failed'
	fi
	if ! tar xf "$tar_file" "$directory/root" -O | \
		ubiupdatevol "/dev/$root_ubivol" -s "$rootfs_length" -; then
		aw1000_upgrade_abort 'rootfs UBI write failed'
	fi

	aw1000_finish_upgrade "$kernel_length" "$rootfs_length"
}

platform_do_upgrade() {
	[ "$(board_name)" = 'arcadyan,aw1000' ] || aw1000_upgrade_abort 'unsupported board'
	aw1000_nand_upgrade_tar "$1"
}
