# Mask.msg
# One segmentation mask from the perception service (ROBOAI-60).
string source        # 'auto' (automatic masks), 'name' (a name from the request), 'point' (a point prompt)
int32 prompt         # index of the name or point in the request; -1 for 'auto'
string label         # the detector's phrase for 'name', empty otherwise
float32 score        # detector score for 'name', SAM's predicted IoU otherwise
int32[4] bbox        # x0, y0, x1, y1 in pixels, inclusive
uint32[] rle         # run lengths over the image, row major, starting with a run of 0s
