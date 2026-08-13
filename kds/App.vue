<script>
function enableAppFullscreen() {
	// #ifdef APP-PLUS
	if (typeof plus === 'undefined') {
		return
	}
	try {
		plus.navigator.setFullscreen(true)
		if (plus.os.name === 'Android') {
			const main = plus.android.runtimeMainActivity()
			const WindowManager = plus.android.importClass('android.view.WindowManager')
			const View = plus.android.importClass('android.view.View')
			const window = main.getWindow()
			window.setFlags(
				WindowManager.LayoutParams.FLAG_FULLSCREEN,
				WindowManager.LayoutParams.FLAG_FULLSCREEN
			)
			const decorView = window.getDecorView()
			decorView.setSystemUiVisibility(
				View.SYSTEM_UI_FLAG_FULLSCREEN
				| View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
				| View.SYSTEM_UI_FLAG_LAYOUT_STABLE
				| View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
			)
		}
	} catch (error) {
		console.error('[全屏] 设置失败:', error)
	}
	// #endif
}

export default {
	onLaunch() {
		enableAppFullscreen()
	},
	onShow() {
		enableAppFullscreen()
	}
}
</script>

<style>
	/* Light ops console tokens — shared Hub / Settings visual language */
	page {
		--ops-bg: #eef1f4;
		--ops-surface: #ffffff;
		--ops-ink: #1a2332;
		--ops-muted: #5b6573;
		--ops-line: #d5dbe3;
		--ops-accent: #0b6bcb;
		--ops-accent-soft: #e7f1fb;
		--ops-ok: #1f8a4c;
		--ops-ok-soft: #e6f6ec;
		--ops-warn: #c47a00;
		--ops-danger: #c62828;
		--ops-danger-soft: #fdecea;
		--ops-shadow: 0 1px 2px rgba(26, 35, 50, 0.06), 0 8px 24px rgba(26, 35, 50, 0.06);
		--ops-radius: 14px;
		--ops-font: "IBM Plex Sans", "Noto Sans SC", "PingFang SC", "Helvetica Neue", sans-serif;
		font-family: var(--ops-font);
		color: var(--ops-ink);
		background-color: var(--ops-bg);
	}
</style>
