
package com.hellododo.assistant

import android.Manifest
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.speech.RecognizerIntent
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.util.Locale

private const val TAG = "HelloDodo"
private const val TTS_TAG = "HelloDodoTTS"
private const val BACKEND_URL = "http://192.168.1.4:8000"

private val Background = Color(0xFF0B1020)
private val Panel = Color(0xFF151D30)
private val Accent = Color(0xFF8B9CFF)
private val Muted = Color(0xFF9AA7C0)
private val UserBubble = Color(0xFF29365A)

private data class ChatMessage(
    val text: String,
    val fromDodo: Boolean
)

class MainActivity : ComponentActivity() {

    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var pendingSpeech: String? = null

    private var ttsInitAttempt = 0
    private var speechRetryCount = 0
    private var pendingOverlayPermission = false

    private val mainHandler = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        initializeTts()
        enableEdgeToEdge()

        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme(
                    primary = Accent,
                    background = Background,
                    surface = Panel
                )
            ) {
                DodoScreen(
                    onSpeakReply = ::speakReply
                )
            }
        }
    }

    fun requestOverlayPermissionAndStart() {
        if (
            Build.VERSION.SDK_INT < Build.VERSION_CODES.M ||
            Settings.canDrawOverlays(this)
        ) {
            WakeWordService.start(applicationContext)
            return
        }

        pendingOverlayPermission = true

        try {
            val intent = Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:$packageName")
            )
            startActivity(intent)
        } catch (exception: Exception) {
            pendingOverlayPermission = false
            Log.e(
                TAG,
                "Could not open overlay permission settings.",
                exception
            )
        }
    }

    override fun onResume() {
        super.onResume()

        if (!pendingOverlayPermission) return

        pendingOverlayPermission = false

        val overlayGranted =
            Build.VERSION.SDK_INT < Build.VERSION_CODES.M ||
                    Settings.canDrawOverlays(this)

        val microphoneGranted = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED

        if (overlayGranted && microphoneGranted) {
            try {
                WakeWordService.start(applicationContext)
            } catch (exception: Exception) {
                Log.e(
                    TAG,
                    "Could not start wake-word service.",
                    exception
                )
            }
        } else {
            Log.i(TAG, "Overlay or microphone permission was not granted.")
        }
    }

    private fun initializeTts(replaceExisting: Boolean = false) {
        if (isFinishing || isDestroyed) return

        if (replaceExisting) {
            ttsReady = false

            try {
                tts?.stop()
            } catch (exception: Exception) {
                Log.w(
                    TTS_TAG,
                    "Could not stop previous TTS engine.",
                    exception
                )
            }

            try {
                tts?.shutdown()
            } catch (exception: Exception) {
                Log.w(
                    TTS_TAG,
                    "Could not shut down previous TTS engine.",
                    exception
                )
            }

            tts = null
        }

        val attempt = ++ttsInitAttempt

        Log.i(TTS_TAG, "Requesting TTS initialization. Attempt=$attempt")

        try {
            val newEngine = TextToSpeech(applicationContext) { status ->
                mainHandler.post {
                    if (
                        attempt != ttsInitAttempt ||
                        isFinishing ||
                        isDestroyed
                    ) {
                        Log.d(TTS_TAG, "Ignoring stale TTS callback.")
                        return@post
                    }

                    val engine = tts

                    if (engine == null) {
                        Log.e(
                            TTS_TAG,
                            "TTS callback received without an engine."
                        )
                        return@post
                    }

                    if (status != TextToSpeech.SUCCESS) {
                        ttsReady = false
                        Log.e(
                            TTS_TAG,
                            "TTS initialization failed: status=$status"
                        )
                        return@post
                    }

                    engine.setOnUtteranceProgressListener(
                        object : UtteranceProgressListener() {

                            override fun onStart(utteranceId: String?) {
                                Log.i(
                                    TTS_TAG,
                                    "Speech started: id=$utteranceId"
                                )
                            }

                            override fun onDone(utteranceId: String?) {
                                Log.i(
                                    TTS_TAG,
                                    "Speech completed: id=$utteranceId"
                                )
                            }

                            override fun onError(utteranceId: String?) {
                                Log.e(
                                    TTS_TAG,
                                    "Speech failed: id=$utteranceId"
                                )
                            }

                            override fun onError(
                                utteranceId: String?,
                                errorCode: Int
                            ) {
                                Log.e(
                                    TTS_TAG,
                                    "Speech failed: id=$utteranceId, error=$errorCode"
                                )
                            }
                        }
                    )

                    var languageStatus = engine.setLanguage(Locale.US)

                    if (
                        languageStatus == TextToSpeech.LANG_MISSING_DATA ||
                        languageStatus == TextToSpeech.LANG_NOT_SUPPORTED
                    ) {
                        Log.w(
                            TTS_TAG,
                            "English (US) unavailable; trying device default language."
                        )
                        languageStatus =
                            engine.setLanguage(Locale.getDefault())
                    }

                    ttsReady =
                        languageStatus != TextToSpeech.LANG_MISSING_DATA &&
                                languageStatus != TextToSpeech.LANG_NOT_SUPPORTED

                    Log.i(
                        TTS_TAG,
                        "TTS initialized: ready=$ttsReady, " +
                                "languageStatus=$languageStatus, " +
                                "engine=${engine.defaultEngine}"
                    )

                    if (ttsReady) {
                        val queuedText = pendingSpeech

                        if (!queuedText.isNullOrBlank()) {
                            pendingSpeech = null
                            speakReply(queuedText)
                        }
                    } else {
                        Log.e(
                            TTS_TAG,
                            "No supported TTS language. Check Android Text-to-speech settings."
                        )
                    }
                }
            }

            tts = newEngine

            Log.i(
                TTS_TAG,
                "TTS engine object assigned. Attempt=$attempt"
            )
        } catch (exception: Exception) {
            ttsReady = false
            Log.e(TTS_TAG, "Could not create TTS engine.", exception)
        }
    }

    private fun speakReply(text: String) {
        val cleanText = text.trim()
        if (cleanText.isEmpty()) return

        if (Looper.myLooper() != Looper.getMainLooper()) {
            mainHandler.post { speakReply(cleanText) }
            return
        }

        if (isFinishing || isDestroyed) {
            Log.w(TTS_TAG, "Activity is closing; speech was not started.")
            return
        }

        val engine = tts

        if (engine == null || !ttsReady) {
            Log.w(
                TTS_TAG,
                "TTS not ready; queuing reply (${cleanText.length} chars)."
            )
            pendingSpeech = cleanText
            return
        }

        Log.i(TTS_TAG, "Speaking actual reply: $cleanText")

        try {
            val result = engine.speak(
                cleanText,
                TextToSpeech.QUEUE_FLUSH,
                Bundle(),
                "dodo_reply_${System.currentTimeMillis()}"
            )

            if (result == TextToSpeech.ERROR) {
                Log.e(
                    TTS_TAG,
                    "TTS rejected speech; checking engine binding."
                )

                pendingSpeech = cleanText
                ttsReady = false

                if (speechRetryCount < 1) {
                    speechRetryCount++
                    initializeTts(replaceExisting = true)
                } else {
                    Log.e(
                        TTS_TAG,
                        "Speech retry exhausted; reply remains queued."
                    )
                }
            } else {
                speechRetryCount = 0
                Log.i(TTS_TAG, "Speech request accepted.")
            }
        } catch (exception: Exception) {
            Log.e(
                TTS_TAG,
                "TTS speak() threw an exception.",
                exception
            )

            pendingSpeech = cleanText
            ttsReady = false

            if (speechRetryCount < 1) {
                speechRetryCount++
                initializeTts(replaceExisting = true)
            }
        }
    }

    override fun onDestroy() {
        ttsInitAttempt++
        ttsReady = false
        pendingSpeech = null

        mainHandler.removeCallbacksAndMessages(null)

        try {
            tts?.stop()
        } catch (exception: Exception) {
            Log.w(
                TTS_TAG,
                "TTS stop failed during destruction.",
                exception
            )
        }

        try {
            tts?.shutdown()
        } catch (exception: Exception) {
            Log.w(
                TTS_TAG,
                "TTS shutdown failed during destruction.",
                exception
            )
        }

        tts = null
        super.onDestroy()
    }
}

private suspend fun sendMessageToBackend(message: String): String =
    withContext(Dispatchers.IO) {
        var connection: HttpURLConnection? = null

        try {
            connection = (
                    URL("$BACKEND_URL/chat").openConnection()
                            as HttpURLConnection
                    ).apply {
                    requestMethod = "POST"
                    connectTimeout = 10000
                    readTimeout = 180000
                    doOutput = true

                    setRequestProperty(
                        "Content-Type",
                        "application/json; charset=utf-8"
                    )
                    setRequestProperty("Accept", "application/json")
                }

            val requestBody = JSONObject()
                .put("message", message)
                .toString()

            connection.outputStream.use { output ->
                output.write(requestBody.toByteArray(Charsets.UTF_8))
            }

            val statusCode = connection.responseCode

            val responseStream = if (statusCode in 200..299) {
                connection.inputStream
            } else {
                connection.errorStream
            }

            val responseText = responseStream
                ?.bufferedReader()
                ?.use { it.readText() }
                .orEmpty()

            if (statusCode !in 200..299) {
                val detail = try {
                    JSONObject(responseText).optString(
                        "detail",
                        responseText
                    )
                } catch (_: Exception) {
                    responseText
                }

                Log.e(TAG, "Backend HTTP $statusCode: $detail")

                throw Exception(
                    "Backend HTTP $statusCode: " +
                            detail.ifBlank { "Unknown error" }
                )
            }

            val json = JSONObject(responseText)
            val reply = json.optString("reply").trim()

            if (reply.isEmpty()) {
                Log.e(
                    TAG,
                    "HTTP $statusCode returned no usable reply. Response body: $responseText"
                )

                throw Exception(
                    "Laptop returned HTTP $statusCode, " +
                            "but the reply field was empty or missing."
                )
            }

            Log.i(
                TAG,
                "Backend reply received (${reply.length} chars): $reply"
            )

            reply
        } catch (exception: Exception) {
            if (
                exception.message?.startsWith("Backend HTTP ") == true ||
                exception.message?.contains("reply field was empty") == true
            ) {
                throw exception
            }

            val detail = when (exception) {
                is SocketTimeoutException ->
                    "Laptop response timeout. Backend aur Wi-Fi check karo."

                is ConnectException ->
                    "Laptop se connection nahi hua. Backend aur IP check karo."

                else ->
                    "Laptop connection failed: " +
                            (exception.message ?: "unknown error")
            }

            Log.e(TAG, detail, exception)
            throw Exception(detail, exception)
        } finally {
            connection?.disconnect()
        }
    }


@Composable
private fun DodoScreen(
    onSpeakReply: (String) -> Unit
) {
    var isListening by remember { mutableStateOf(false) }
    var isSending by remember { mutableStateOf(false) }
    var draft by remember { mutableStateOf("") }

    var backendStatus by remember {
        mutableStateOf("Laptop connection not tested")
    }

    var voiceStatus by remember {
        mutableStateOf("Ready when you are")
    }

    val context = LocalContext.current
    var wakeWordActive by remember { mutableStateOf(false) }

    val messages = remember {
        mutableStateListOf(
            ChatMessage(
                "Bhai, main Dodo hoon! Apna voice assistant setup karte hain.",
                true
            ),
            ChatMessage(
                "Voice input ready hai. Ab main laptop backend se connect kar sakta hoon.",
                true
            )
        )
    }

    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()

    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) {
            listState.animateScrollToItem(messages.lastIndex)
        }
    }

    val wakeWordPermissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestMultiplePermissions()
    ) {
        val microphoneGranted = ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED

        if (microphoneGranted) {
            val activity = context as? MainActivity

            if (activity != null) {
                try {
                    val needsOverlayPermission =
                        Build.VERSION.SDK_INT >= Build.VERSION_CODES.M &&
                                !Settings.canDrawOverlays(context)

                    activity.requestOverlayPermissionAndStart()

                    voiceStatus = if (needsOverlayPermission) {
                        "Allow display over other apps, then return here."
                    } else {
                        "Starting Hey Jarvis wake-word listener..."
                    }
                } catch (exception: Exception) {
                    wakeWordActive = false
                    voiceStatus = "Could not start wake-word listener."
                    Log.e(TAG, voiceStatus, exception)
                }
            } else {
                wakeWordActive = false
                voiceStatus = "Could not access the app activity."
            }
        } else {
            wakeWordActive = false
            voiceStatus = "Microphone permission is required."
        }
    }

    fun startWakeWordMode() {
        val missingPermissions = mutableListOf<String>()

        if (
            ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.RECORD_AUDIO
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            missingPermissions.add(Manifest.permission.RECORD_AUDIO)
        }

        if (
            Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            missingPermissions.add(Manifest.permission.POST_NOTIFICATIONS)
        }

        if (missingPermissions.isNotEmpty()) {
            wakeWordPermissionLauncher.launch(
                missingPermissions.toTypedArray()
            )
            return
        }

        val activity = context as? MainActivity

        if (activity == null) {
            voiceStatus = "Could not access the app activity."
            return
        }

        try {
            val needsOverlayPermission =
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.M &&
                        !Settings.canDrawOverlays(context)

            activity.requestOverlayPermissionAndStart()

            if (needsOverlayPermission) {
                voiceStatus =
                    "Allow display over other apps, then return here."
            } else {
                wakeWordActive = true
                voiceStatus =
                    "Starting Hey Jarvis wake-word listener..."
            }
        } catch (exception: Exception) {
            wakeWordActive = false
            voiceStatus = "Could not start wake-word listener."
            Log.e(TAG, voiceStatus, exception)
        }
    }

    fun stopWakeWordMode() {
        try {
            WakeWordService.stop(context)
            wakeWordActive = false
            voiceStatus = "Wake-word listening stopped."
        } catch (exception: Exception) {
            voiceStatus = "Could not stop wake-word listener."
            Log.e(TAG, "Could not stop wake-word service", exception)
        }
    }

    fun sendToLaptop(text: String) {
        val message = text.trim()

        if (message.isEmpty() || isSending) return

        messages.add(ChatMessage(message, false))

        isSending = true
        backendStatus = "Connecting to laptop..."
        voiceStatus = "Sending message to laptop..."

        Log.i(TAG, "Sending message to backend: $message")

        scope.launch {
            try {
                val reply = sendMessageToBackend(message)

                messages.add(ChatMessage(reply, true))
                backendStatus = "Connected · response received"
                voiceStatus = "Reply received · speaking..."

                onSpeakReply(reply)

                voiceStatus = "Laptop replied successfully"
            } catch (exception: Exception) {
                val errorMessage =
                    exception.message ?: "Could not reach laptop"

                Log.e(
                    TAG,
                    "Message exchange failed: $errorMessage",
                    exception
                )

                backendStatus = "Connection failed"
                voiceStatus = errorMessage

                messages.add(
                    ChatMessage(
                        "Bhai, laptop se connect nahi ho paya. $errorMessage",
                        true
                    )
                )
            } finally {
                isSending = false
            }
        }
    }

    DisposableEffect(context) {
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(
                receiverContext: Context?,
                intent: Intent?
            ) {
                if (intent?.action != WakeWordService.ACTION_EVENT) return

                when (
                    intent.getStringExtra(WakeWordService.EXTRA_STATUS)
                ) {
                    "listening" -> {
                        wakeWordActive = true
                        voiceStatus = "Listening for Hey Jarvis..."
                        backendStatus = "Wake-word listener active"
                    }

                    "wake_detected" -> {
                        wakeWordActive = true
                        voiceStatus = "Hey Jarvis detected · speak now"
                    }

                    "recognizing" -> {
                        voiceStatus = "Listening for your command..."
                    }

                    "processing" -> {
                        val command = intent.getStringExtra(
                            WakeWordService.EXTRA_COMMAND
                        )?.trim()

                        if (!command.isNullOrBlank()) {
                            messages.add(ChatMessage(command, false))
                        }

                        voiceStatus =
                            "Sending voice command to laptop..."
                        backendStatus = "Processing voice command..."
                    }

                    "reply" -> {
                        val reply = intent.getStringExtra(
                            WakeWordService.EXTRA_REPLY
                        )?.trim()

                        if (!reply.isNullOrBlank()) {
                            messages.add(ChatMessage(reply, true))
                            backendStatus =
                                "Connected · response received"
                            voiceStatus =
                                "Reply received · listening again"
                        }
                    }

                    "error" -> {
                        val message = intent.getStringExtra("message")
                            ?: "Wake-word listener error"

                        voiceStatus = message

                        if (
                            message.contains(
                                "backend",
                                ignoreCase = true
                            ) ||
                            message.contains(
                                "laptop",
                                ignoreCase = true
                            ) ||
                            message.contains(
                                "connection",
                                ignoreCase = true
                            )
                        ) {
                            backendStatus = "Connection failed"
                        }

                        Log.w(TAG, "Wake-word service: $message")
                    }
                }
            }
        }

        val filter = IntentFilter(WakeWordService.ACTION_EVENT)

        if (Build.VERSION.SDK_INT >= 33) {
            context.registerReceiver(
                receiver,
                filter,
                Context.RECEIVER_NOT_EXPORTED
            )
        } else {
            @Suppress("DEPRECATION")
            context.registerReceiver(receiver, filter)
        }

        onDispose {
            try {
                context.unregisterReceiver(receiver)
            } catch (exception: Exception) {
                Log.w(
                    TAG,
                    "Could not unregister wake-word receiver",
                    exception
                )
            }
        }
    }

    val voiceInputLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.StartActivityForResult()
    ) { result ->
        isListening = false

        if (result.resultCode == Activity.RESULT_OK) {
            val spokenText = result.data
                ?.getStringArrayListExtra(
                    RecognizerIntent.EXTRA_RESULTS
                )
                ?.firstOrNull()
                ?.trim()

            if (!spokenText.isNullOrBlank()) {
                Log.i(TAG, "Recognized speech: $spokenText")
                sendToLaptop(spokenText)
            } else {
                voiceStatus = "No speech detected. Please try again."
            }
        } else {
            voiceStatus =
                "Voice input cancelled. Tap the mic to try again."
        }
    }

    fun startGoogleVoiceInput() {
        val intent = Intent(
            RecognizerIntent.ACTION_RECOGNIZE_SPEECH
        ).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
            )
            putExtra(
                RecognizerIntent.EXTRA_PROMPT,
                "Bolo bhai, main sun raha hoon..."
            )
            putExtra(
                RecognizerIntent.EXTRA_MAX_RESULTS,
                3
            )
        }

        try {
            isListening = true
            voiceStatus = "Opening Google voice input..."
            voiceInputLauncher.launch(intent)
        } catch (exception: ActivityNotFoundException) {
            isListening = false
            voiceStatus = "Compatible voice input app is unavailable."
            Log.e(TAG, voiceStatus, exception)
        } catch (exception: Exception) {
            isListening = false
            voiceStatus = "Could not open voice input."
            Log.e(TAG, voiceStatus, exception)
        }
    }

    Scaffold(
        containerColor = Background,
        bottomBar = {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Background)
                    .padding(horizontal = 16.dp)
                    .padding(bottom = 20.dp, top = 8.dp)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    OutlinedTextField(
                        value = draft,
                        onValueChange = { draft = it },
                        modifier = Modifier.weight(1f),
                        placeholder = {
                            Text(
                                "Dodo se baat kar...",
                                color = Muted
                            )
                        },
                        shape = RoundedCornerShape(18.dp),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedTextColor = Color.White,
                            unfocusedTextColor = Color.White,
                            focusedBorderColor = Accent,
                            unfocusedBorderColor = Color(0xFF35405A),
                            cursorColor = Accent
                        ),
                        maxLines = 3
                    )

                    Spacer(Modifier.width(8.dp))

                    Button(
                        onClick = {
                            val text = draft.trim()

                            if (text.isNotEmpty() && !isSending) {
                                draft = ""
                                sendToLaptop(text)
                            }
                        },
                        enabled = draft.isNotBlank() && !isSending,
                        shape = RoundedCornerShape(16.dp),
                        contentPadding = PaddingValues(
                            horizontal = 16.dp,
                            vertical = 16.dp
                        )
                    ) {
                        Text(if (isSending) "..." else "Send")
                    }
                }

                Spacer(Modifier.height(8.dp))

                Text(
                    text = voiceStatus,
                    color = Muted,
                    fontSize = 11.sp,
                    modifier = Modifier.align(
                        Alignment.CenterHorizontally
                    )
                )
            }
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 20.dp)
        ) {
            Spacer(Modifier.height(18.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(48.dp)
                        .background(
                            Color(0xFF252E50),
                            CircleShape
                        ),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        "D",
                        color = Accent,
                        fontSize = 27.sp,
                        fontWeight = FontWeight.Bold
                    )
                }

                Spacer(Modifier.width(12.dp))

                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        "Hello Dodo",
                        color = Color.White,
                        fontSize = 25.sp,
                        fontWeight = FontWeight.Bold
                    )

                    Text(
                        "Your personal voice assistant",
                        color = Muted,
                        fontSize = 12.sp
                    )
                }

                Surface(
                    shape = CircleShape,
                    color = Color(0xFF342B23)
                ) {
                    Text(
                        "LOCAL",
                        color = Color(0xFFFFC58B),
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(
                            horizontal = 10.dp,
                            vertical = 7.dp
                        )
                    )
                }
            }

            Spacer(Modifier.height(24.dp))

            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(24.dp),
                colors = CardDefaults.cardColors(
                    containerColor = Panel
                )
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(
                            vertical = 22.dp,
                            horizontal = 16.dp
                        ),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Box(
                        modifier = Modifier
                            .size(104.dp)
                            .background(
                                if (wakeWordActive || isListening) {
                                    Color(0xFF394A86)
                                } else {
                                    Color(0xFF222C49)
                                },
                                CircleShape
                            ),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            if (wakeWordActive || isListening) "●" else "🎙",
                            fontSize = if (
                                wakeWordActive || isListening
                            ) {
                                35.sp
                            } else {
                                40.sp
                            },
                            color = Accent
                        )
                    }

                    Spacer(Modifier.height(16.dp))

                    Text(
                        when {
                            wakeWordActive -> "Wake-word mode active"
                            isListening -> "Voice input opening..."
                            isSending -> "Talking to laptop..."
                            else -> "Ready when you are"
                        },
                        color = Color.White,
                        fontSize = 19.sp,
                        fontWeight = FontWeight.SemiBold
                    )

                    Spacer(Modifier.height(6.dp))

                    Text(
                        backendStatus,
                        color = Muted,
                        fontSize = 12.sp
                    )

                    Spacer(Modifier.height(18.dp))

                    Button(
                        onClick = { startGoogleVoiceInput() },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(52.dp),
                        shape = RoundedCornerShape(16.dp),
                        enabled = !isListening &&
                                !isSending &&
                                !wakeWordActive,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Accent,
                            contentColor = Color(0xFF11172C)
                        )
                    ) {
                        Text(
                            when {
                                isListening -> "Opening voice input..."
                                isSending -> "Waiting for laptop..."
                                else -> "Start listening"
                            },
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }

                    Spacer(Modifier.height(10.dp))

                    Button(
                        onClick = {
                            if (wakeWordActive) {
                                stopWakeWordMode()
                            } else {
                                startWakeWordMode()
                            }
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(52.dp),
                        shape = RoundedCornerShape(16.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = if (wakeWordActive) {
                                Color(0xFF8B4545)
                            } else {
                                Color(0xFF29365A)
                            },
                            contentColor = Color.White
                        )
                    ) {
                        Text(
                            if (wakeWordActive) {
                                "Stop wake-word listening"
                            } else {
                                "Enable Hey Jarvis"
                            },
                            fontSize = 15.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }
                }
            }

            Spacer(Modifier.height(20.dp))

            Text(
                "CONNECTION STATUS",
                color = Muted,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 1.5.sp
            )

            Spacer(Modifier.height(10.dp))

            Row(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                StatusCard(
                    title = "Laptop",
                    value = if (backendStatus.startsWith("Connected")) {
                        "Connected"
                    } else {
                        backendStatus
                    },
                    modifier = Modifier.weight(1f)
                )

                StatusCard(
                    title = "Voice output",
                    value = "Google TTS",
                    modifier = Modifier.weight(1f)
                )
            }

            Spacer(Modifier.height(22.dp))

            Text(
                "CONVERSATION",
                color = Muted,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 1.5.sp
            )

            Spacer(Modifier.height(8.dp))

            LazyColumn(
                state = listState,
                modifier = Modifier
                    .fillMaxSize()
                    .padding(bottom = 8.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(messages) { message ->
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = if (message.fromDodo) {
                            Arrangement.Start
                        } else {
                            Arrangement.End
                        }
                    ) {
                        Surface(
                            color = if (message.fromDodo) {
                                Panel
                            } else {
                                UserBubble
                            },
                            shape = RoundedCornerShape(18.dp),
                            modifier = Modifier.fillMaxWidth(0.88f)
                        ) {
                            Column(
                                modifier = Modifier.padding(14.dp)
                            ) {
                                Text(
                                    if (message.fromDodo) "DODO" else "YOU",
                                    color = Accent,
                                    fontSize = 10.sp,
                                    fontWeight = FontWeight.Bold
                                )

                                Spacer(Modifier.height(4.dp))

                                Text(
                                    message.text,
                                    color = Color.White,
                                    fontSize = 14.sp,
                                    lineHeight = 21.sp
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun StatusCard(
    title: String,
    value: String,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(
            containerColor = Panel
        )
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text(
                title,
                color = Muted,
                fontSize = 12.sp
            )

            Spacer(Modifier.height(7.dp))

            Text(
                value,
                color = Color.White,
                fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold
            )
        }
    }
}