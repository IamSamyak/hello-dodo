
package com.hellododo.assistant

import android.content.Context
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

object DodoOverlayManager {

    private const val TAG = "DodoOverlay"
    private val handler = Handler(Looper.getMainLooper())

    private var windowManager: WindowManager? = null
    private var root: FrameLayout? = null
    private var iconView: TextView? = null
    private var titleView: TextView? = null
    private var detailView: TextView? = null

    private val dismissRunnable = Runnable { hideNow() }

    fun handleEvent(
        context: Context,
        status: String,
        command: String? = null,
        reply: String? = null,
        message: String? = null
    ) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            handler.post {
                handleEvent(context, status, command, reply, message)
            }
            return
        }

        when (status) {
            "wake_detected" -> show(
                context,
                "Hey Dodo",
                "Speak your command...",
                "●"
            )

            "recognizing" -> show(
                context,
                "Listening...",
                "I'm listening to you",
                "●"
            )

            "processing" -> show(
                context,
                "Thinking...",
                command?.takeIf { it.isNotBlank() }
                    ?: "Processing your command",
                "✦"
            )

            "reply" -> show(
                context,
                "Dodo",
                reply?.takeIf { it.isNotBlank() } ?: "Done",
                "✦"
            )

            "speaking" -> show(
                context,
                "Dodo is speaking",
                reply?.takeIf { it.isNotBlank() } ?: "Here is your answer",
                "♫"
            )

            "speech_done" -> {
                handler.removeCallbacks(dismissRunnable)
                handler.postDelayed(dismissRunnable, 1200L)
            }

            "error" -> {
                show(
                    context,
                    "Dodo needs attention",
                    message?.takeIf { it.isNotBlank() } ?: "Something went wrong",
                    "!"
                )
                handler.removeCallbacks(dismissRunnable)
                handler.postDelayed(dismissRunnable, 4500L)
            }

            "stopped" -> hide()

            // Wake-word standby should not keep the overlay visible.
            "listening", "starting" -> Unit
        }
    }

    private fun show(
        context: Context,
        title: String,
        detail: String,
        icon: String
    ) {
        handler.removeCallbacks(dismissRunnable)

        val appContext = context.applicationContext

        if (
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.M &&
            !Settings.canDrawOverlays(appContext)
        ) {
            Log.w(TAG, "Overlay permission is not granted.")
            return
        }

        if (root == null) {
            createOverlay(appContext)
        }

        val currentRoot = root ?: return

        iconView?.text = icon
        titleView?.text = title
        detailView?.text = detail

        if (currentRoot.visibility != View.VISIBLE) {
            currentRoot.visibility = View.VISIBLE
            currentRoot.alpha = 0f
            currentRoot.translationY = 40f
            currentRoot.animate()
                .alpha(1f)
                .translationY(0f)
                .setDuration(220L)
                .start()
        }
    }

    private fun createOverlay(context: Context) {
        val wm = context.getSystemService(
            Context.WINDOW_SERVICE
        ) as WindowManager

        val outer = FrameLayout(context).apply {
            setPadding(
                dp(context, 18),
                dp(context, 8),
                dp(context, 18),
                dp(context, 28)
            )
            visibility = View.VISIBLE
        }

        val card = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(
                dp(context, 16),
                dp(context, 13),
                dp(context, 16),
                dp(context, 13)
            )
            background = roundedBackground(
                color = 0xF21A2238.toInt(),
                stroke = 0xFF7185E8.toInt()
            )
            elevation = dp(context, 12).toFloat()
        }

        val icon = TextView(context).apply {
            text = "●"
            textSize = 23f
            gravity = Gravity.CENTER
            setTextColor(0xFFB5C1FF.toInt())
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(0xFF303E68.toInt())
            }
        }

        card.addView(
            icon,
            LinearLayout.LayoutParams(
                dp(context, 42),
                dp(context, 42)
            )
        )

        val textColumn = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val title = TextView(context).apply {
            text = "Hey Dodo"
            textSize = 15f
            typeface = Typeface.create(
                "sans-serif-medium",
                Typeface.NORMAL
            )
            setTextColor(0xFFFFFFFF.toInt())
            maxLines = 1
        }

        val detail = TextView(context).apply {
            text = "Speak your command..."
            textSize = 12f
            setTextColor(0xFFB9C3D8.toInt())
            maxLines = 2
            ellipsize = android.text.TextUtils.TruncateAt.END
        }

        textColumn.addView(title)
        textColumn.addView(
            detail,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                topMargin = dp(context, 3)
            }
        )

        card.addView(
            textColumn,
            LinearLayout.LayoutParams(
                0,
                LinearLayout.LayoutParams.WRAP_CONTENT,
                1f
            ).apply {
                marginStart = dp(context, 12)
            }
        )

        outer.addView(
            card,
            FrameLayout.LayoutParams(
                dp(context, 340).coerceAtMost(
                    context.resources.displayMetrics.widthPixels - dp(context, 36)
                ),
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            )
        )

        val windowType =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            } else {
                @Suppress("DEPRECATION")
                WindowManager.LayoutParams.TYPE_PHONE
            }

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            windowType,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            y = 0
        }

        try {
            wm.addView(outer, params)

            windowManager = wm
            root = outer
            iconView = icon
            titleView = title
            detailView = detail
        } catch (exception: Exception) {
            Log.e(TAG, "Could not create overlay.", exception)
            root = null
            windowManager = null
        }
    }

    fun hide() {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            handler.post { hideNow() }
        } else {
            hideNow()
        }
    }

    private fun hideNow() {
        handler.removeCallbacks(dismissRunnable)

        val currentRoot = root ?: return

        currentRoot.animate().cancel()

        try {
            currentRoot.animate()
                .alpha(0f)
                .translationY(30f)
                .setDuration(180L)
                .withEndAction {
                    removeOverlay(currentRoot)
                }
                .start()
        } catch (exception: Exception) {
            Log.w(TAG, "Overlay hide animation failed.", exception)
            removeOverlay(currentRoot)
        }
    }

    private fun removeOverlay(view: View) {
        if (root !== view) return

        try {
            windowManager?.removeView(view)
        } catch (exception: Exception) {
            Log.w(TAG, "Could not remove overlay view.", exception)
        } finally {
            root = null
            windowManager = null
            iconView = null
            titleView = null
            detailView = null
        }
    }

    private fun roundedBackground(
        color: Int,
        stroke: Int
    ) = GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        cornerRadius = 26f
        setColor(color)
        setStroke(1, stroke)
    }

    private fun dp(context: Context, value: Int): Int =
        (value * context.resources.displayMetrics.density).toInt()
}