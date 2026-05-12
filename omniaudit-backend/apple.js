import * as ImagePicker from 'expo-image-picker';
import { useState } from 'react';
const handleScan = async (category) => {
  // 1. Ask for permission to use the camera
  const { status } = await ImagePicker.requestCameraPermissionsAsync();
  if (status !== 'granted') {
    alert('Sorry, we need camera permissions to make this work!');
    return;
  }

  // 2. Launch the camera
  let result = await ImagePicker.launchCameraAsync({
    allowsEditing: true,
    quality: 0.7, // Compressed for faster upload
  });

  if (!result.canceled) {
    const imageUri = result.assets[0].uri;
    
    // 3. Prepare the data to send to your Python Backend
    let formData = new FormData();
    formData.append("file", {
      uri: imageUri,
      name: "bill_scan.jpg",
      type: "image/jpeg",
    });
    formData.append("category", category);

    try {
      console.log(`Sending ${category} scan to backend...`);
      // Use your IPv4 address here (run 'ipconfig' in terminal to find it)
      const response = await fetch("http://192.168.1.XX:8000/analyze-document/", {
        method: "POST",
        body: formData,
        headers: {
          "Content-Type": "multipart/form-data",
        },
      });

      const data = await response.json();

      if (data.verdict === "error") {
        alert(data.message); // This catches the "Incorrect Upload" AI error!
      } else {
        // Log the result for now - next we will navigate to a result screen!
        console.log("Audit Result:", data);
        alert(`Verdict: ${data.verdict}\nPotential Savings: ₹${data.savings}`);
      }
    } catch (error) {
      console.error(error);
      alert("Could not connect to the server. Is your Python backend running?");
    }
  }
};