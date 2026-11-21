package com.team23.aplabmc;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.webkit.GeolocationPermissions;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.IOException;
import java.io.InputStream;

/**
 * Apla BMC: the web app in ../../app, bundled as assets and shown full-screen.
 * Files are served from https://appassets.androidplatform.net/ so the page is a secure origin
 * (location and clipboard need one); every other link opens outside the app.
 */
public class MainActivity extends Activity {
    private static final String HOST = "appassets.androidplatform.net";
    private static final String START_URL = "https://" + HOST + "/www/index.html";
    private static final int REQ_LOCATION = 1;
    private static final int REQ_PHOTO = 2;

    private WebView web;
    private GeolocationPermissions.Callback geoCallback;
    private String geoOrigin;
    private ValueCallback<Uri[]> photoCallback;
    private Uri cameraUri;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        web = new WebView(this);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true); // "Me too" taps, language choice
        s.setGeolocationEnabled(true);
        s.setAllowFileAccess(false);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if (!HOST.equals(url.getHost())) return null; // fonts etc. go to the network
                String path = url.getPath();
                if (path == null || path.contains("..")) return notFound();
                try {
                    InputStream in = getAssets().open(path.substring(1));
                    return new WebResourceResponse(mimeType(path), "utf-8", in);
                } catch (IOException e) {
                    return notFound();
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if (HOST.equals(url.getHost())) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, url)); // WhatsApp, MyBMC, RTI portal
                } catch (ActivityNotFoundException ignored) {
                    // nothing installed can open it
                }
                return true;
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
                if (hasLocationPermission()) {
                    callback.invoke(origin, true, false);
                    return;
                }
                geoOrigin = origin;
                geoCallback = callback;
                requestPermissions(new String[] {
                    Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION
                }, REQ_LOCATION);
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                return openPhotoPicker(callback, params);
            }
        });

        if (savedInstanceState != null) web.restoreState(savedInstanceState);
        else web.loadUrl(START_URL);
    }

    /** "Saw a pothole? Take a photo": offer the camera (saved to Pictures/AplaBMC) and the gallery. */
    private boolean openPhotoPicker(ValueCallback<Uri[]> callback, WebChromeClient.FileChooserParams params) {
        if (photoCallback != null) photoCallback.onReceiveValue(null);
        photoCallback = callback;
        cameraUri = null;
        Intent chooser = Intent.createChooser(params.createIntent(), null);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) { // no storage permission needed from Android 10
            ContentValues values = new ContentValues();
            values.put(MediaStore.Images.Media.DISPLAY_NAME, "apla_bmc_" + System.currentTimeMillis() + ".jpg");
            values.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
            values.put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/AplaBMC");
            cameraUri = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
            if (cameraUri != null) {
                Intent camera = new Intent(MediaStore.ACTION_IMAGE_CAPTURE)
                    .putExtra(MediaStore.EXTRA_OUTPUT, cameraUri)
                    .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[] {camera});
            }
        }
        try {
            startActivityForResult(chooser, REQ_PHOTO);
            return true;
        } catch (ActivityNotFoundException e) {
            finishPhoto(null);
            return false;
        }
    }

    private void finishPhoto(Uri[] result) {
        boolean usedCamera = result != null && cameraUri != null && cameraUri.equals(result[0]);
        if (cameraUri != null && !usedCamera) getContentResolver().delete(cameraUri, null, null); // empty camera slot
        if (photoCallback != null) photoCallback.onReceiveValue(result);
        photoCallback = null;
        cameraUri = null;
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_PHOTO) return;
        Uri[] result = null;
        if (resultCode == RESULT_OK) {
            if (data != null && data.getData() != null) result = new Uri[] {data.getData()}; // gallery
            else if (cameraUri != null) result = new Uri[] {cameraUri}; // camera wrote to our slot
        }
        finishPhoto(result);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQ_LOCATION || geoCallback == null) return;
        boolean granted = false;
        for (int r : grantResults) granted |= r == PackageManager.PERMISSION_GRANTED;
        geoCallback.invoke(geoOrigin, granted, false); // denied → the page shows the sample road
        geoCallback = null;
        // the page's first request may have timed out while the prompt was open; let it try again
        if (granted) web.evaluateJavascript("window.aplaRetryLocation && window.aplaRetryLocation()", null);
    }

    private boolean hasLocationPermission() {
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
            || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack(); // screens are #hash pages, so this walks back through them
        else super.onBackPressed();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    private static WebResourceResponse notFound() {
        return new WebResourceResponse("text/plain", "utf-8", 404, "Not Found", null, null);
    }

    private static String mimeType(String path) {
        if (path.endsWith(".html")) return "text/html";
        if (path.endsWith(".css")) return "text/css";
        if (path.endsWith(".js")) return "application/javascript";
        if (path.endsWith(".json")) return "application/json";
        if (path.endsWith(".png")) return "image/png";
        if (path.endsWith(".svg")) return "image/svg+xml";
        return "application/octet-stream";
    }
}
