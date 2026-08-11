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
	/*每个页面公共css */
</style>
