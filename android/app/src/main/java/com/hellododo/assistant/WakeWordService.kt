
package com.hellododo.assistant

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import androidx.core.app.NotificationCompat
import com.openwakeword.OpenWakeWord
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class WakeWordService : Service() {

    companion object {
        const val ACTION_EVENT = "com.hellododo.assistant.WAKE_WORD_EVENT"
        const val EXTRA_STATUS = "status"
        const val EXTRA_COMMAND = "command"
        const val EXTRA_REPLY = "reply"
        const val EXTRA_MESSAGE = "message"

        private const val ACTION_START =
            "com.hellododo.assistant.action.START_WAKE_WORD"

        private const val ACTION_STOP =
            "com.hellododo.assistant.action.STOP_WAKE_WORD"

        private const val CHANNEL_ID = "hello_dodo_wake_word"
        private const val NOTIFICATION_ID = 4201
        private const val BACKEND_URL = "http://192.168.1.4:8000/chat"

        private const val TAG = "DodoWakeDebug"
        private const val WAKE_THRESHOLD = 0.2f

        fun start(context: Context) {
            val intent = Intent(context, WakeWordService::class.java)
                .setAction(ACTION_START)

            androidx.core.content.ContextCompat.startForegroundService(
                context,
                intent
            )
        }

        fun stop(context: Context) {
            val intent = Intent(context, WakeWordService::class.java)
                .setAction(ACTION_STOP)

            context.startService(intent)
        }
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private val executor: ExecutorService = Executors.newSingleThreadExecutor()
    private val isHandlingCommand = AtomicBoolean(false)

    private var wakeWordDetector: OpenWakeWord? = null
    private var speechRecognizer: SpeechRecognizer? = null
    private var textToSpeech: TextToSpeech? = null

    @Volatile
    private var serviceRunning = false

    @Volatile
    private var waitingForSpeech = false

    @Volatile
    private var lastScoreLogTime = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        initializeTextToSpeech()
    }

    override fun onStartCommand(
        intent: Intent?,
        flags: Int,
        startId: Int
    ): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopWakeWordService()
                return START_NOT_STICKY
            }

            ACTION_START, null -> {
                if (!serviceRunning) {
                    startForegroundSafely()
                    serviceRunning = true

                    sendEvent(
                        "starting",
                        message = "Starting wake-word detector..."
                    )

                    startWakeWordDetection()
                }
            }
        }

        return START_NOT_STICKY
    }

    private fun startForegroundSafely() {
        val notification = buildNotification()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Hello Dodo wake-word listener",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description =
                    "Keeps Hello Dodo ready for the Hey Jarvis wake phrase."
                setShowBadge(false)
            }

            getSystemService(NotificationManager::class.java)
                .createNotificationChannel(channel)
        }
    }

    private fun buildNotification(): Notification {
        val openAppIntent = Intent(this, MainActivity::class.java)

        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            openAppIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                        PendingIntent.FLAG_IMMUTABLE
                    } else {
                        0
                    }
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Hello Dodo is active")
            .setContentText("Listening for Hey Jarvis")
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun initializeTextToSpeech() {
        textToSpeech = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                textToSpeech?.language = Locale("en", "IN")

                textToSpeech?.setOnUtteranceProgressListener(
                    object : UtteranceProgressListener() {
                        override fun onStart(utteranceId: String?) = Unit

                        override fun onDone(utteranceId: String?) {
                            mainHandler.post {
                                if (serviceRunning) {
                                    isHandlingCommand.set(false)
                                    startWakeWordDetection()
                                }
                            }
                        }

                        @Deprecated("Deprecated in Java")
                        override fun onError(utteranceId: String?) {
                            mainHandler.post {
                                if (serviceRunning) {
                                    isHandlingCommand.set(false)
                                    startWakeWordDetection()
                                }
                            }
                        }

                        override fun onError(
                            utteranceId: String?,
                            errorCode: Int
                        ) {
                            mainHandler.post {
                                if (serviceRunning) {
                                    isHandlingCommand.set(false)
                                    startWakeWordDetection()
                                }
                            }
                        }
                    }
                )
            } else {
                Log.e(TAG, "TextToSpeech initialization failed: $status")
            }
        }
    }

    private fun startWakeWordDetection() {
        if (!serviceRunning || waitingForSpeech || isHandlingCommand.get()) {
            return
        }

        releaseWakeWordDetector()

        try {
            lastScoreLogTime = 0L

            Log.d(TAG, "Creating Hey Jarvis detector")

            sendEvent(
                "listening",
                message = "Listening for Hey Jarvis..."
            )

            wakeWordDetector = OpenWakeWord.Builder(this)
                .setModel(OpenWakeWord.BuiltInModel.HEY_JARVIS)
                .setThreshold(WAKE_THRESHOLD)
                .build()

            Log.d(TAG, "Detector created; threshold=$WAKE_THRESHOLD")

            wakeWordDetector?.start { score ->

                // Log at most once per second to keep Logcat readable.
                val now = System.currentTimeMillis()

                if (now - lastScoreLogTime >= 1000L) {
                    lastScoreLogTime = now
                    Log.d(TAG, "Wake score=$score")
                }

                if (
                    score >= WAKE_THRESHOLD &&
                    serviceRunning &&
                    isHandlingCommand.compareAndSet(false, true)
                ) {
                    Log.i(TAG, "Wake threshold reached: score=$score")

                    mainHandler.post {
                        onWakeWordDetected()
                    }
                }
            }

            Log.d(TAG, "Wake-word detector started")
        } catch (exception: Exception) {
            isHandlingCommand.set(false)

            Log.e(TAG, "Could not start wake detector", exception)

            sendEvent(
                "error",
                message = "Could not start wake-word detection: " +
                        (exception.message ?: "unknown error")
            )
        }
    }


    private fun onWakeWordDetected() {
        if (!serviceRunning) {
            isHandlingCommand.set(false)
            return
        }

        Log.i(TAG, "Hey Jarvis detected; preparing command recognition")

        sendEvent(
            "wake_detected",
            message = "Hey Jarvis detected. Speak your command after the tone."
        )

        releaseWakeWordDetector()

        // Allow the wake phrase to finish before Android starts command recognition.
        mainHandler.postDelayed({
            if (serviceRunning && isHandlingCommand.get()) {
                startCommandRecognition()
            }
        }, 700)
    }

    private fun startCommandRecognition() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            sendEvent(
                "error",
                message = "Speech recognition is unavailable on this phone."
            )

            retryWakeWordDetection()
            return
        }

        try {
            waitingForSpeech = true

            speechRecognizer?.destroy()
            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this)

            speechRecognizer?.setRecognitionListener(
                object : RecognitionListener {
                    override fun onReadyForSpeech(
                        params: android.os.Bundle?
                    ) {
                        Log.d(TAG, "Speech recognizer ready")

                        sendEvent(
                            "recognizing",
                            message = "Listening for your command..."
                        )
                    }

                    override fun onBeginningOfSpeech() {
                        Log.d(TAG, "Command speech began")
                    }

                    override fun onRmsChanged(rmsdB: Float) = Unit

                    override fun onBufferReceived(buffer: ByteArray?) = Unit

                    override fun onEndOfSpeech() {
                        Log.d(TAG, "Command speech ended")

                        sendEvent(
                            "processing",
                            message = "Processing your command..."
                        )
                    }

                    override fun onError(error: Int) {
                        Log.e(
                            TAG,
                            "Speech recognition error=$error"
                        )

                        waitingForSpeech = false

                        speechRecognizer?.destroy()
                        speechRecognizer = null

                        sendEvent(
                            "error",
                            message = speechErrorMessage(error)
                        )

                        retryWakeWordDetection()
                    }

                    override fun onResults(results: android.os.Bundle?) {
                        waitingForSpeech = false

                        val command = results
                            ?.getStringArrayList(
                                SpeechRecognizer.RESULTS_RECOGNITION
                            )
                            ?.firstOrNull()
                            ?.trim()
                            .orEmpty()

                        Log.d(TAG, "Recognized command=$command")

                        speechRecognizer?.destroy()
                        speechRecognizer = null

                        if (command.isBlank()) {
                            sendEvent(
                                "error",
                                message = "I didn't catch that. Try again."
                            )

                            retryWakeWordDetection()
                        } else {
                            sendEvent("processing", command = command)
                            sendCommandToBackend(command)
                        }
                    }

                    override fun onPartialResults(
                        partialResults: android.os.Bundle?
                    ) = Unit

                    override fun onEvent(
                        eventType: Int,
                        params: android.os.Bundle?
                    ) = Unit
                }
            )

            val recognitionIntent = Intent(
                RecognizerIntent.ACTION_RECOGNIZE_SPEECH
            ).apply {
                putExtra(
                    RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                    RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
                )
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-IN")
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
            }

            speechRecognizer?.startListening(recognitionIntent)
        } catch (exception: Exception) {
            waitingForSpeech = false

            speechRecognizer?.destroy()
            speechRecognizer = null

            Log.e(TAG, "Could not start command recognition", exception)

            sendEvent(
                "error",
                message = "Could not start speech recognition: " +
                        (exception.message ?: "unknown error")
            )

            retryWakeWordDetection()
        }
    }

    private fun sendCommandToBackend(command: String) {
        executor.execute {
            var connection: HttpURLConnection? = null

            try {
                connection = URL(BACKEND_URL)
                    .openConnection() as HttpURLConnection

                connection.requestMethod = "POST"
                connection.connectTimeout = 15000
                connection.readTimeout = 60000
                connection.doOutput = true

                connection.setRequestProperty(
                    "Content-Type",
                    "application/json; charset=utf-8"
                )

                val payload = JSONObject()
                    .put("message", command)
                    .toString()

                OutputStreamWriter(
                    connection.outputStream,
                    Charsets.UTF_8
                ).use { writer ->
                    writer.write(payload)
                }

                val responseCode = connection.responseCode

                val responseStream =
                    if (responseCode in 200..299) {
                        connection.inputStream
                    } else {
                        connection.errorStream
                    }

                val responseBody = responseStream?.use { stream ->
                    BufferedReader(
                        InputStreamReader(stream, Charsets.UTF_8)
                    ).readText()
                }.orEmpty()

                if (responseCode !in 200..299) {
                    throw IllegalStateException(
                        "Backend returned HTTP $responseCode: $responseBody"
                    )
                }

                val reply = JSONObject(responseBody)
                    .optString("reply")
                    .trim()

                if (reply.isBlank()) {
                    throw IllegalStateException(
                        "The backend response did not contain a reply."
                    )
                }

                mainHandler.post {
                    if (serviceRunning) {
                        sendEvent(
                            "reply",
                            command = command,
                            reply = reply
                        )

                        speakReply(reply)
                    }
                }
            } catch (exception: Exception) {
                Log.e(TAG, "Backend request failed", exception)

                mainHandler.post {
                    if (serviceRunning) {
                        sendEvent(
                            "error",
                            command = command,
                            message = "Laptop/backend connection failed: " +
                                    (exception.message ?: "unknown error")
                        )

                        retryWakeWordDetection()
                    }
                }
            } finally {
                connection?.disconnect()
            }
        }
    }

    private fun speakReply(reply: String) {
        val tts = textToSpeech

        if (tts == null) {
            sendEvent(
                "error",
                message = "Text-to-speech is not ready."
            )

            retryWakeWordDetection()
            return
        }

        sendEvent("speaking", reply = reply)
        val result = tts.speak(
            reply,
            TextToSpeech.QUEUE_FLUSH,
            null,
            "hello_dodo_reply"
        )

        if (result == TextToSpeech.ERROR) {
            sendEvent(
                "error",
                message = "Could not speak the backend reply."
            )

            retryWakeWordDetection()
        }
    }

    private fun retryWakeWordDetection() {
        waitingForSpeech = false

        mainHandler.postDelayed({
            if (serviceRunning) {

                sendEvent("speech_done")
                isHandlingCommand.set(false)
                startWakeWordDetection()
            }
        }, 1200)
    }

    private fun speechErrorMessage(error: Int): String {
        return when (error) {
            SpeechRecognizer.ERROR_AUDIO ->
                "Microphone audio error. Try again."

            SpeechRecognizer.ERROR_CLIENT ->
                "Speech recognition was interrupted."

            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS ->
                "Microphone permission is missing."

            SpeechRecognizer.ERROR_NETWORK,
            SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
                "Android speech recognition needs a working network connection."

            SpeechRecognizer.ERROR_NO_MATCH,
            SpeechRecognizer.ERROR_SPEECH_TIMEOUT ->
                "I didn't hear a command. Try again."

            SpeechRecognizer.ERROR_RECOGNIZER_BUSY ->
                "Speech recognition is busy. Retrying."

            else ->
                "Speech recognition failed (error $error). Retrying."
        }
    }


    private fun sendEvent(
        status: String,
        command: String? = null,
        reply: String? = null,
        message: String? = null
    ) {
        DodoOverlayManager.handleEvent(
            context = this,
            status = status,
            command = command,
            reply = reply,
            message = message
        )

        val intent = Intent(ACTION_EVENT).apply {
            setPackage(packageName)
            putExtra(EXTRA_STATUS, status)

            command?.let { putExtra(EXTRA_COMMAND, it) }
            reply?.let { putExtra(EXTRA_REPLY, it) }
            message?.let { putExtra(EXTRA_MESSAGE, it) }
        }

        sendBroadcast(intent)
    }

    private fun releaseWakeWordDetector() {
        try {
            wakeWordDetector?.stop()
        } catch (exception: Exception) {
            Log.w(TAG, "Error stopping detector", exception)
        }

        try {
            wakeWordDetector?.release()
        } catch (exception: Exception) {
            Log.w(TAG, "Error releasing detector", exception)
        }

        wakeWordDetector = null
    }

    private fun stopWakeWordService() {
        serviceRunning = false
        waitingForSpeech = false
        isHandlingCommand.set(false)

        releaseWakeWordDetector()

        try {
            speechRecognizer?.cancel()
            speechRecognizer?.destroy()
        } catch (exception: Exception) {
            Log.w(TAG, "Error destroying recognizer", exception)
        }

        speechRecognizer = null

        try {
            textToSpeech?.stop()
        } catch (exception: Exception) {
            Log.w(TAG, "Error stopping TTS", exception)
        }

        sendEvent(
            "stopped",
            message = "Wake-word listener stopped."
        )

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }

        stopSelf()
    }

    override fun onDestroy() {
        serviceRunning = false
        waitingForSpeech = false

        mainHandler.removeCallbacksAndMessages(null)
        releaseWakeWordDetector()

        try {
            speechRecognizer?.cancel()
            speechRecognizer?.destroy()
        } catch (exception: Exception) {
            Log.w(TAG, "Error destroying recognizer", exception)
        }

        speechRecognizer = null

        try {
            textToSpeech?.stop()
            textToSpeech?.shutdown()
        } catch (exception: Exception) {
            Log.w(TAG, "Error shutting down TTS", exception)
        }

        textToSpeech = null
        executor.shutdownNow()

        super.onDestroy()
    }
}